"""
api/tickets.py
Routeur billets : achat simple, flux cash (réservation / encaissement /
annulation / liste organisateur), scan & validation d'entrée, listes fan.
"""
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user, log_security_event
from models.event import Event
from models.ticket import Ticket
from models.user import User
from schemas.ticket import BuyTicket, ScanTicket, CashReservation

router = APIRouter()


# ── ACHAT SIMPLE (réservation en attente de paiement) ───────────
@router.post("/tickets/buy")
def buy_ticket(data: BuyTicket, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    event = db.query(Event).filter(Event.id == data.event_id, Event.is_active == True).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    if event.seats_sold >= event.total_seats:
        raise HTTPException(400, "Plus de places disponibles")
    if event.event_date < datetime.utcnow():
        raise HTTPException(400, "Événement déjà passé")
    ticket = Ticket(user_id=current_user.id, event_id=event.id)
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return {
        "message": "Billet réservé",
        "ticket_id": ticket.id,
        "qr_hash": ticket.qr_hash,
        "amount": event.price,
        "currency": "FCFA",
        "instruction": f"Composez #150*50# sur Orange Money pour payer {int(event.price)} FCFA",
        "ussd_code": "#150*50#",
    }


# ── LISTES FAN ──────────────────────────────────────────────────
@router.get("/tickets/my")
def my_tickets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tickets = (
        db.query(Ticket)
        .filter(Ticket.user_id == current_user.id)
        .order_by(Ticket.purchased_at.desc())
        .all()
    )
    return [{
        "ticket_id": t.id,
        "qr_hash": t.qr_hash,
        "payment_status": t.payment_status,
        "payment_method": t.payment_method,
        "event_title": t.event.title if t.event else "",
        "event_date": t.event.event_date if t.event else None,
        "event_location": t.event.location if t.event else "",
        "price": t.cash_amount if t.cash_amount else (t.event.price if t.event else 0),
        "is_used": t.is_used,
        "purchased_at": t.purchased_at,
    } for t in tickets]


@router.get("/tickets/paid")
def my_paid_tickets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tickets = (
        db.query(Ticket)
        .filter(Ticket.user_id == current_user.id, Ticket.payment_status == "paye")
        .order_by(Ticket.purchased_at.desc())
        .all()
    )
    return [{
        "ticket_id": t.id, "qr_hash": t.qr_hash,
        "event_title": t.event.title if t.event else "",
        "event_date": t.event.event_date if t.event else None,
        "event_location": t.event.location if t.event else "",
        "price": t.event.price if t.event else 0,
        "is_used": t.is_used,
        "purchased_at": t.purchased_at,
    } for t in tickets]


# ── RÉSERVATION CASH ────────────────────────────────────────────
@router.post("/tickets/reserve-cash")
def reserve_ticket_cash(data: CashReservation, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    event = db.query(Event).filter(Event.id == data.event_id, Event.is_active == True).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    deadline = event.event_date - timedelta(hours=24)
    if datetime.utcnow() > deadline:
        raise HTTPException(400,
            "Les réservations cash ne sont plus acceptées (moins de 24h avant l'événement). "
            "Vous pouvez encore acheter via Orange Money à l'entrée.")
    if event.event_date < datetime.utcnow():
        raise HTTPException(400, "Événement déjà passé")
    if event.seats_sold >= event.total_seats:
        raise HTTPException(400, "Événement complet")

    nb_reservations = db.query(Ticket).filter(
        Ticket.user_id == current_user.id,
        Ticket.event_id == event.id,
        Ticket.payment_status.in_(["reservation_cash", "paye_cash", "paye"]),
    ).count()
    if nb_reservations >= 3:
        raise HTTPException(400, "Vous avez atteint la limite de 3 réservations pour cet événement.")

    prix_base = event.price
    if data.tier_name and event.ticket_tiers:
        tier = next((t for t in event.ticket_tiers if t.get("name") == data.tier_name), None)
        if not tier:
            raise HTTPException(400, "Catégorie de billet invalide")
        try:
            prix_base = float(tier.get("price", event.price))
        except (ValueError, TypeError):
            prix_base = float(event.price)

    est_gratuit = (prix_base <= 0)
    status_final = "paye" if est_gratuit else "reservation_cash"
    methode_final = "gratuit" if est_gratuit else "cash"
    ref_prefix = "FREE" if est_gratuit else "CASH"

    if data.email and not current_user.email:
        current_user.email = data.email

    new_ticket = Ticket(
        event_id=event.id,
        user_id=current_user.id,
        payment_status=status_final,
        payment_method=methode_final,
        payment_ref=f"{ref_prefix}-{uuid.uuid4().hex[:8].upper()}",
        cash_amount=prix_base,
        reservation_expires_at=deadline if not est_gratuit else None,
    )
    db.add(new_ticket)
    event.seats_sold += 1
    db.commit()
    db.refresh(new_ticket)

    log_security_event(db, "cash_reservation", None, current_user.id,
                       f"Réservation cash événement {event.id}, montant {prix_base}")

    return {
        "success": True,
        "message": "Réservation confirmée. À payer en cash à l'entrée.",
        "ticket_id": new_ticket.id,
        "qr_hash": new_ticket.qr_hash,
        "amount_to_pay": prix_base,
        "event_title": event.title,
        "event_date": event.event_date.isoformat(),
        "expires_at": deadline.isoformat(),
        "instructions": (
            f"Présentez ce QR code à l'entrée de l'événement et payez "
            f"{int(prix_base)} FCFA en cash à l'agent. "
            f"Réservation valable jusqu'au {deadline.strftime('%d/%m/%Y à %H:%M')}."
        ),
    }


@router.post("/tickets/{ticket_id}/confirm-cash-payment")
de
