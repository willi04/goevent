"""
api/tickets.py
Routeur billets : billet gratuit, scan & validation d'entrée, listes fan.
Paiement en ligne uniquement (Orange Money via api/payments.py) — le flux
cash payant (réservation / encaissement / annulation) est supprimé (oct. 2026).
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi import BackgroundTasks
from core.email import envoyer_billet_email
from fastapi import Request
from core.limiter import limiter

from utils.locks import lock_event_for_booking
from utils.promo import lock_promo_for_use, compute_discount
from core.database import get_db
from core.security import get_current_user, log_security_event
from models.event import Event
from models.ticket import Ticket
from models.user import User
from models.promo import PromoUsage
from schemas.ticket import ScanTicket, CashReservation

router = APIRouter()


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
        "failure_reason": t.failure_reason,
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


# ── BILLET GRATUIT (event gratuit ou code promo -100 %) ─────────
# Oct. 2026 : la RÉSERVATION CASH PAYANTE EST SUPPRIMÉE (paiement en ligne
# uniquement). Cette route ne délivre plus que des billets à 0 FCFA.
# /tickets/reserve-cash conservée comme alias : PWA en cache côté fans.
@router.post("/tickets/reserve-free")
@router.post("/tickets/reserve-cash")
@limiter.limit("20/minute")
def reserve_free_ticket(request: Request,
                        data: CashReservation,
                        background_tasks: BackgroundTasks,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    # Verrou pessimiste + check quota atomique (anti-survente)
    event = lock_event_for_booking(db, data.event_id)

    if event.event_date < datetime.utcnow():
        raise HTTPException(400, "Événement déjà passé")

    # Anti-accaparement des places gratuites : 3 billets max par compte et par event.
    nb_billets = db.query(Ticket).filter(
        Ticket.user_id == current_user.id,
        Ticket.event_id == event.id,
        Ticket.payment_status.in_(["paye", "paye_cash"]),
    ).count()
    if nb_billets >= 3:
        raise HTTPException(400, "Vous avez atteint la limite de 3 billets pour cet événement.")

    prix_base = float(event.price)
    if data.tier_name and event.ticket_tiers:
        tier = next((t for t in event.ticket_tiers if t.get("name") == data.tier_name), None)
        if not tier:
            raise HTTPException(400, "Catégorie de billet invalide")
        try:
            prix_base = float(tier.get("price", event.price))
        except (ValueError, TypeError):
            raise HTTPException(400, "Catégorie de billet invalide")

    # Code promo : seul moyen de rendre gratuit un billet payant.
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

    if prix_base > 0:
        # Plus de cash : tout billet payant passe par Orange Money.
        # (Le rollback de la session libère les verrous event/promo.)
        raise HTTPException(400, "Ce billet est payant : réglez-le par Orange Money.")

    if data.email and not current_user.email:
        current_user.email = data.email

    new_ticket = Ticket(
        event_id=event.id,
        user_id=current_user.id,
        payment_status="paye",
        payment_method="gratuit",
        payment_ref=f"FREE-{uuid.uuid4().hex[:8].upper()}",
        cash_amount=0,
    )
    db.add(new_ticket)
    event.seats_sold += 1  # sous verrou lock_event_for_booking
    db.flush()  # new_ticket.id avant la PromoUsage

    if promo_obj:
        db.add(PromoUsage(
            promo_id=promo_obj.id,
            user_id=current_user.id,
            ticket_id=new_ticket.id,
            discount_fcfa=discount_fcfa,
            status="confirmed",
        ))
        promo_obj.used_count += 1

    db.commit()
    db.refresh(new_ticket)

    log_security_event(db, "free_ticket", None, current_user.id,
                       f"Billet gratuit événement {event.id}" + (f" (promo {promo_obj.id})" if promo_obj else ""))
    if current_user.email:
        background_tasks.add_task(
            envoyer_billet_email,
            email_client=current_user.email,
            nom_client=current_user.full_name or "Client",
            ticket=new_ticket,
            event=event,
            amount=0,
        )

    return {
        "success": True,
        "message": "Billet gratuit confirmé.",
        "ticket_id": new_ticket.id,
        "qr_hash": new_ticket.qr_hash,
        "amount_to_pay": 0,
        "event_title": event.title,
        "event_date": event.event_date.isoformat(),
        "instructions": "Présentez ce QR code à l'entrée de l'événement.",
    }


def _can_operate_event(user: User, event: Event) -> bool:
    """
    Contrôle d'accès opérationnel jour J (scan, lecture billet).
    organizer : propriétaire de l'event. agent : assigné à CET event
    (agent_event_id n'est posé que par l'orga propriétaire, cf create_agent).
    org_name n'est PAS un critère : champ éditable via PUT /auth/me.
    """
    if event is None:
        return False
    if user.role in ("organizer", "organisation"):
        return event.organizer_id == user.id
    if user.role == "agent":
        return user.agent_event_id is not None and user.agent_event_id == event.id
    return False


# ── SCAN & VALIDATION D'ENTRÉE ──────────────────────────────────
@router.post("/scan")
@limiter.limit("120/minute")
def valider_billet(request: Request, data: ScanTicket, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    if current_user.role not in ("agent", "organizer", "organisation"):
        raise HTTPException(status_code=403, detail="Accès refusé. Réservé aux contrôleurs.")

    # Verrou ligne : 2 agents qui scannent le même QR simultanément →
    # un seul passe, l'autre voit "DÉJÀ UTILISÉ".
    ticket = (
        db.query(Ticket)
        .filter(Ticket.qr_hash == data.qr_data)
        .with_for_update()
        .first()
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Billet introuvable (Faux billet ou QR mal lu).")

    # Fix NameError `event` (agents → 500 systématique depuis juin) +
    # scoping orga : avant, tout orga pouvait "brûler" les billets de n'importe quel event.
    if not _can_operate_event(current_user, ticket.event):
        log_security_event(db, "scan_forbidden", request, current_user.id,
                           f"Scan refusé ticket {ticket.id} (event {ticket.event_id})")
        raise HTTPException(403, "Ce billet appartient à un autre événement.")

    if ticket.is_used:
        date_used = ticket.used_at.strftime("%H:%M:%S") if ticket.used_at else "?"
        raise HTTPException(status_code=400, detail=f"Alerte : Billet DÉJÀ UTILISÉ à {date_used} !")

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
        if current_user.agent_event_id != event.id:
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
