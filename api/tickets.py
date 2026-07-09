"""
api/tickets.py
Routeur billets : achat simple, flux cash (réservation / encaissement /
annulation / liste organisateur), scan & validation d'entrée, listes fan.
"""
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi import BackgroundTasks
from core.email import envoyer_billet_email
from fastapi import Request
from core.limiter import limiter

from utils.locks import lock_event_for_booking, lock_event_for_release
from utils.promo import (
    lock_promo_for_use, compute_discount,
    confirm_promo_usage, release_promo_usage,
)
from core.database import get_db
from core.security import get_current_user, log_security_event
from models.event import Event
from models.ticket import Ticket
from models.user import User
from models.promo import PromoUsage
from schemas.ticket import BuyTicket, ScanTicket, CashReservation

router = APIRouter()


# ── ACHAT SIMPLE (réservation en attente de paiement) ───────────
@router.post("/tickets/buy")
@limiter.limit("20/minute")
def buy_ticket(
                request: Request, 
                data: BuyTicket, db: Session = Depends(get_db),
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
@limiter.limit("20/minute")
def reserve_ticket_cash(request: Request,
                        data: CashReservation,
                        background_tasks: BackgroundTasks,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    # P0 — Verrou pessimiste + check quota atomique (anti-survente)
    event = lock_event_for_booking(db, data.event_id)

    if event.event_date < datetime.utcnow():
        raise HTTPException(400, "Événement déjà passé")
    # Deadline -24h : applicable UNIQUEMENT aux events payants
    # (logistique billetterie cash à l'entrée). Events gratuits = pas de friction.
    deadline = event.event_date - timedelta(hours=24)
    if event.price > 0 and datetime.utcnow() > deadline:
        raise HTTPException(400,
            "Les réservations cash ne sont plus acceptées (moins de 24h avant l'événement). "
            "Vous pouvez encore acheter via Orange Money à l'entrée.")

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

    # APPLICATION CODE PROMO (Chantier 19.A)
    # Cas particulier flux cash : si la promo rend prix=0, on accepte
    # (génération d'un billet gratuit, statut "paye" direct).
    promo_obj = None
    discount_fcfa = 0
    if data.promo_code:
        promo_obj = lock_promo_for_use(
            db,
            user_id=current_user.id,
            event_id=event.id,
            tier_name=data.tier_name,
            code=data.promo_code,
            organizer_id=event.organizer_id,
        )
        discount_fcfa = compute_discount(promo_obj, prix_base)
        prix_base = max(0.0, prix_base - discount_fcfa)

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
    db.flush()  # pour avoir new_ticket.id avant la PromoUsage

    # Insertion PromoUsage (Chantier 19.A)
    # - Flux cash payant : statut "reserved" (sera "confirmed" à l'encaissement par l'agent)
    # - Flux gratuit (promo rend prix=0) : statut "confirmed" direct (billet finalisé)
    if promo_obj:
        usage_status = "confirmed" if est_gratuit else "reserved"
        db.add(PromoUsage(
            promo_id=promo_obj.id,
            user_id=current_user.id,
            ticket_id=new_ticket.id,
            discount_fcfa=discount_fcfa,
            status=usage_status,
        ))
        promo_obj.used_count += 1

    db.commit()
    db.refresh(new_ticket)

    log_security_event(db, "cash_reservation", None, current_user.id,
                       f"Réservation cash événement {event.id}, montant {prix_base}")
    # ── Email confirmation billet (BackgroundTasks : ne bloque pas la réponse) ──
    # Couvre les 3 cas de reserve-cash : gratuit → "paye", cash → "reservation_cash",
    # confirmation admin future → "paye_cash". Le fan reçoit un email dans les 3 cas.
    if current_user.email:
        background_tasks.add_task(
            envoyer_billet_email,
            email_client=current_user.email,
            nom_client=current_user.full_name or "Client",
            ticket=new_ticket,
            event=event,
            amount=int(prix_base),
        )
        
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
@limiter.limit("30/minute")
def confirm_cash_payment(request: Request, ticket_id: int, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    if current_user.role not in ("organizer", "organisation", "agent"):
        raise HTTPException(403, "Accès réservé aux organisateurs et agents")

    # P0 — Verrou pessimiste sur le ticket : si 2 agents scannent le même QR
    # cash simultanément, un seul peut commit l'encaissement.
    ticket = (
        db.query(Ticket)
        .filter(Ticket.id == ticket_id)
        .with_for_update()
        .first()
    )
    if not ticket:
        raise HTTPException(404, "Billet introuvable")

    if ticket.payment_status != "reservation_cash":
        raise HTTPException(400, f"Ce billet n'est pas en attente de paiement cash (statut: {ticket.payment_status})")

    event = ticket.event
    if current_user.role in ("organizer", "organisation"):
        if event.organizer_id != current_user.id:
            raise HTTPException(403, "Cet événement n'est pas le vôtre")
    elif current_user.role == "agent":
        if current_user.agent_event_id and current_user.agent_event_id != event.id:
            raise HTTPException(403, "Vous n'êtes pas assigné à cet événement")
        org_name_required = event.organizer_user.org_name or event.organizer_user.full_name
        if current_user.org_name != org_name_required:
            raise HTTPException(403, "Vous n'êtes pas un agent de cet organisateur")

    ticket.payment_status = "paye_cash"
    ticket.cash_collected_by = current_user.id
    ticket.cash_collected_at = datetime.utcnow()

    # Confirmation de l'éventuelle promo associée (Chantier 19.A)
    confirm_promo_usage(db, ticket.id)

    from models.payment import Payment
    cash_payment = Payment(
        user_id=ticket.user_id,
        ticket_id=ticket.id,
        amount=ticket.cash_amount or 0,
        base_price=ticket.cash_amount or 0,
        platform_fee=0,
        organizer_amount=ticket.cash_amount or 0,
        status="completed",
        transaction_id=ticket.payment_ref or f"CASH-{ticket.id}",
    )
    db.add(cash_payment)
    db.commit()

    log_security_event(db, "cash_collected", None, current_user.id,
                       f"Cash encaissé pour ticket {ticket.id}, montant {ticket.cash_amount}")

    return {
        "success": True,
        "message": f"Paiement de {int(ticket.cash_amount)} FCFA encaissé.",
        "ticket": {
            "id": ticket.id,
            "fan_name": ticket.owner.full_name,
            "amount": ticket.cash_amount,
            "event_title": event.title,
        },
    }


@router.get("/tickets/{ticket_id}/info")
def get_ticket_info(ticket_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    if current_user.role not in ("organizer", "organisation", "agent"):
        raise HTTPException(403, "Accès refusé")
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(404, "Ticket introuvable")
    return {
        "id": ticket.id,
        "qr_hash": ticket.qr_hash,
        "payment_status": ticket.payment_status,
    }


@router.delete("/tickets/{ticket_id}/cancel-reservation")
def cancel_cash_reservation(ticket_id: int, db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(404, "Réservation introuvable")

    is_owner = (ticket.user_id == current_user.id)
    is_event_organizer = (ticket.event.organizer_id == current_user.id
                          and current_user.role in ("organizer", "organisation"))
    if not (is_owner or is_event_organizer):
        raise HTTPException(403, "Vous n'avez pas le droit d'annuler cette réservation")

    if ticket.payment_status != "reservation_cash":
        raise HTTPException(400, "Seules les réservations en attente de paiement peuvent être annulées.")

   # P0 — Verrou pessimiste sur Event pour décrément atomique
    event = lock_event_for_release(db, ticket.event_id)
    if event:
        event.seats_sold = max(0, event.seats_sold - 1)

    # Libération promo AVANT delete (Chantier 19.A)
    # Critique : sans ça, le ON DELETE CASCADE supprimerait la PromoUsage
    # sans décrémenter used_count, laissant le code artificiellement épuisé.
    release_promo_usage(db, ticket.id)

    db.delete(ticket)
    db.commit()
    return {"success": True, "message": "Réservation annulée. La place a été libérée."}


@router.get("/organizer/cash-reservations")
def get_my_cash_reservations(db: Session = Depends(get_db),
                             current_user: User = Depends(get_current_user)):
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Réservé aux organisateurs")

    my_events = db.query(Event).filter(Event.organizer_id == current_user.id).all()
    my_event_ids = [e.id for e in my_events]
    if not my_event_ids:
        return {"reservations": [], "total_count": 0, "total_amount": 0}

    reservations = (
        db.query(Ticket)
        .filter(Ticket.event_id.in_(my_event_ids), Ticket.payment_status == "reservation_cash")
        .order_by(Ticket.purchased_at.desc())
        .all()
    )
    total_amount = sum(t.cash_amount or 0 for t in reservations)

    return {
        "reservations": [{
            "id": r.id,
            "qr_hash": r.qr_hash,
            "fan_name": r.owner.full_name,
            "fan_phone": r.owner.phone_number,
            "fan_email": r.owner.email,
            "event_id": r.event_id,
            "event_title": r.event.title if r.event else "?",
            "event_date": r.event.event_date.isoformat() if r.event else None,
            "amount": r.cash_amount,
            "reserved_at": r.purchased_at.isoformat(),
            "expires_at": r.reservation_expires_at.isoformat() if r.reservation_expires_at else None,
        } for r in reservations],
        "total_count": len(reservations),
        "total_amount": total_amount,
    }


# ── SCAN & VALIDATION D'ENTRÉE ──────────────────────────────────
@router.post("/scan")
def valider_billet(data: ScanTicket, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    if current_user.role not in ("agent", "organizer", "organisation"):
        raise HTTPException(status_code=403, detail="Accès refusé. Réservé aux contrôleurs.")

    ticket = db.query(Ticket).filter(Ticket.qr_hash == data.qr_data).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Billet introuvable (Faux billet ou QR mal lu).")

    if current_user.role == "agent":
        if current_user.agent_event_id and current_user.agent_event_id != ticket.event_id:
            raise HTTPException(status_code=403, detail="Vous n'êtes pas assigné à cet événement !")

    if ticket.is_used:
        date_used = ticket.used_at.strftime("%H:%M:%S") if ticket.used_at else "?"
        raise HTTPException(status_code=400, detail=f"Alerte : Billet DÉJÀ UTILISÉ à {date_used} !")

    if ticket.payment_status == "reservation_cash":
        return {
            "valid": False,
            "requires_cash_payment": True,
            "ticket_id": ticket.id,
            "fan_name": ticket.owner.full_name,
            "fan_phone": ticket.owner.phone_number,
            "amount_to_collect": ticket.cash_amount,
            "event": ticket.event.title,
            "message": f"À ENCAISSER : {int(ticket.cash_amount)} FCFA",
            "instructions": "Encaissez le cash, puis cliquez sur 'Confirmer l'encaissement' pour valider l'entrée.",
        }

    if ticket.payment_status not in ("paye", "paye_cash"):
        raise HTTPException(status_code=400, detail=f"Billet non valide (statut: {ticket.payment_status}).")

    try:
        ticket.is_used = True
        ticket.used_at = datetime.utcnow()
        db.commit()
        return {
            "valid": True,
            "requires_cash_payment": False,
            "message": f"Validé ! {ticket.owner.full_name} peut entrer.",
            "event": ticket.event.title,
            "payment_method": ticket.payment_method or "online",
        }
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur interne lors de la sauvegarde.")


@router.get("/tickets/scan/stats/{event_id}")
def scan_stats(event_id: int, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    # P0 — Restriction : seul agent assigné / organizer de l'event / admin
    if current_user.role == "admin":
        pass
    elif current_user.role in ("organizer", "organisation"):
        if event.organizer_id != current_user.id:
            raise HTTPException(403, "Cet événement n'est pas le vôtre")
    elif current_user.role == "agent":
        if current_user.agent_event_id and current_user.agent_event_id != event.id:
            raise HTTPException(403, "Vous n'êtes pas assigné à cet événement")
        org_name_required = event.organizer_user.org_name or event.organizer_user.full_name
        if current_user.org_name != org_name_required:
            raise HTTPException(403, "Vous n'êtes pas un agent de cet organisateur")
    else:
        raise HTTPException(403, "Accès refusé")

    # Billets validés = paye (NGSER + gratuits ont ce statut) + paye_cash (legacy)
    total_confirmed = db.query(Ticket).filter(
        Ticket.event_id == event_id,
        Ticket.payment_status.in_(["paye", "paye_cash"]),
    ).count()

    # Personnes déjà scannées et entrées
    total_entered = db.query(Ticket).filter(
        Ticket.event_id == event_id,
        Ticket.is_used == True,
    ).count()

    total_remaining = max(0, total_confirmed - total_entered)
    taux = round((total_entered / total_confirmed * 100), 1) if total_confirmed else 0.0

    return {
        "event_title": event.title,
        "total_confirmed": total_confirmed,
        "total_entered": total_entered,
        "total_remaining": total_remaining,
        "entry_rate_pct": taux,
        # Compatibilité ancien contrat
        "billets_vendus": total_confirmed,
        "personnes_entrees": total_entered,
        "personnes_restantes": total_remaining,
        "taux_entree": f"{taux:.1f}%",
    }

# ── ROUTE PUBLIQUE : consultation billet sans authentification ──
# Lien de secours pour fan qui a perdu accès à son compte (perte téléphone,
# oubli PIN, achat pour un tiers). qr_hash est un UUID v4 128 bits, non-devinable
# en pratique — rate limit 30/min pour éviter énumération brute-force résiduelle.
@router.get("/ticket/view")
@limiter.limit("30/minute")
def view_ticket_public(request: Request, qr_hash: str, db: Session = Depends(get_db)):
    """
    Retourne les infos d'un billet à partir de son qr_hash, sans auth.
    Utilisée par ticket_view.html (lien magique dans l'email confirmation).
    """
    ticket = db.query(Ticket).filter(Ticket.qr_hash == qr_hash).first()
    if not ticket:
        raise HTTPException(404, "Billet introuvable")

    if ticket.payment_status not in ("paye", "paye_cash"):
        raise HTTPException(409, "Ce billet n'est pas encore validé")

    event = db.query(Event).filter(Event.id == ticket.event_id).first()

    return {
        "qr_hash": ticket.qr_hash,
        "event_title": event.title if event else None,
        "event_date": event.event_date.isoformat() if event and event.event_date else None,
        "event_location": event.location if event else None,
        "is_used": ticket.is_used,
        "used_at": ticket.used_at.isoformat() if ticket.used_at else None,
    }
