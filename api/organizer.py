"""
api/organizer.py
Routeur organisateur : demande de retrait (payout), historique des retraits,
statistiques de ventes, demande d'annulation d'événement, gestion des agents.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user, log_security_event, hash_pin
from models.event import Event
from models.ticket import Ticket
from models.user import User
from models.payment import Payment, PayoutRequest
from models.partner import CancellationRequest
from schemas.partner import CancelRequestCreate
from schemas.auth import AgentCreate
from utils.serializers import _event_dict

router = APIRouter()


# ── DEMANDE DE RETRAIT ──────────────────────────────────────────
@router.post("/organizer/payout-request")
async def create_payout_request(data: dict, db: Session = Depends(get_db),
                                user: User = Depends(get_current_user)):
    if user.role not in ("organizer", "organisation"):
        raise HTTPException(status_code=403, detail="Accès réservé aux organisateurs")

    event_id = data.get("event_id")
    amount = data.get("amount")
    phone_number = data.get("phone_number")

    if not event_id or not amount or amount <= 0:
        raise HTTPException(400, "Données invalides")

    if phone_number:
        user.orange_money = phone_number

    event = db.query(Event).filter(Event.id == event_id, Event.organizer_id == user.id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    cagnotte_reelle = db.query(func.sum(Payment.organizer_amount)).join(
        Ticket, Payment.ticket_id == Ticket.id
    ).filter(Ticket.event_id == event.id, Payment.status == "completed").scalar() or 0.0

    existing_requests = db.query(PayoutRequest).filter(
        PayoutRequest.event_id == event_id,
        PayoutRequest.status.in_(["en_attente", "paye"]),
    ).all()

    deja_demande = sum(r.amount for r in existing_requests)
    montant_disponible = cagnotte_reelle - deja_demande

    if montant_disponible <= 0:
        raise HTTPException(400, "Vous avez déjà retiré la totalité de votre cagnotte.")
    if amount > montant_disponible:
        raise HTTPException(400, "Montant demandé supérieur au disponible.")

    new_request = PayoutRequest(event_id=event_id, organizer_id=user.id, amount=amount, status="en_attente")
    db.add(new_request)
    db.commit()

    return {"status": "success", "message": "Demande envoyée ! L'admin la validera sous 48h."}


# ── HISTORIQUE DES RETRAITS ─────────────────────────────────────
@router.get("/organizer/payouts")
def get_my_payouts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès refusé")

    payouts = (
        db.query(PayoutRequest)
        .filter(PayoutRequest.organizer_id == current_user.id)
        .order_by(PayoutRequest.created_at.desc())
        .all()
    )

    # Résolution des titres d'événements en UNE requête (anti N+1)
    event_ids = {p.event_id for p in payouts}
    events_map = {}
    if event_ids:
        events = db.query(Event).filter(Event.id.in_(event_ids)).all()
        events_map = {e.id: e for e in events}

    return [{
        "id": p.id,
        "event_id": p.event_id,
        "event_title": events_map[p.event_id].title if p.event_id in events_map else "Événement inconnu",
        "amount": p.amount,
        "status": p.status,
        "created_at": p.created_at.isoformat(),
    } for p in payouts]


# ── STATS ORGANISATEUR ──────────────────────────────────────────
@router.get("/organizer/stats")
def organizer_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role not in ("artiste", "organizer", "agent"):
        raise HTTPException(403, "Réservé aux organisateurs")

    events = db.query(Event).filter(Event.organizer_id == current_user.id).all()

    total_sold = sum(e.seats_sold for e in events)
    active_events = [e for e in events if e.is_active and e.event_date >= datetime.utcnow()]

    total_cagnotte_globale = 0.0
    for e in events:
        cagnotte_event = db.query(func.sum(Payment.organizer_amount)).join(
            Ticket, Payment.ticket_id == Ticket.id
        ).filter(Ticket.event_id == e.id, Payment.status == "completed").scalar() or 0.0
        total_cagnotte_globale += cagnotte_event

    events_data = []
    for e in events[:10]:
        cagnotte_event = db.query(func.sum(Payment.organizer_amount)).join(
            Ticket, Payment.ticket_id == Ticket.id
        ).filter(Ticket.event_id == e.id, Payment.status == "completed").scalar() or 0.0

        events_data.append({
            **_event_dict(e),
            "revenue": e.seats_sold * e.price,
            "cagnotte": cagnotte_event,
            "fill_rate": round(e.seats_sold / e.total_seats * 100, 1) if e.total_seats else 0,
        })

    return {
        "total_events": len(events),
        "active_events": len(active_events),
        "total_sold": total_sold,
        "total_revenue": total_cagnotte_globale,
        "org_name": current_user.org_name or current_user.full_name,
        "org_type": current_user.org_type or current_user.role,
        "events": events_data,
    }


# ── DEMANDE D'ANNULATION ────────────────────────────────────────
@router.post("/organizer/events/{event_id}/cancel-request")
def request_cancellation(event_id: int, data: CancelRequestCreate, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès réservé aux organisateurs")

    if len(data.reason) < 100:
        raise HTTPException(400, "La raison doit faire au moins 100 caractères.")

    event = db.query(Event).filter(Event.id == event_id, Event.organizer_id == current_user.id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    cancel_req = CancellationRequest(
        event_id=event.id,
        organizer_id=current_user.id,
        reason=data.reason,
    )
    db.add(cancel_req)
    db.commit()

    print(f"🚨 ALERTE ANNULATION : L'événement {event.title} demande à être annulé !")

    return {"message": "Demande d'annulation soumise."}


# ── GESTION DES AGENTS (côté organisateur) ──────────────────────
@router.post("/organizer/create-agent")
def create_agent(agent: AgentCreate, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès réservé aux organisateurs")
    if len(agent.pin_code) != 4 or not agent.pin_code.isdigit():
        raise HTTPException(400, "Le PIN doit être 4 chiffres")

    event = db.query(Event).filter(
        Event.id == agent.event_id, Event.organizer_id == current_user.id
    ).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    org_name_actuel = current_user.org_name or current_user.full_name

    existing_user = db.query(User).filter(User.phone_number == agent.phone_number).first()

    if existing_user:
        # CAS A : compte actif d'un autre type
        if existing_user.is_active and existing_user.role != "agent":
            raise HTTPException(400,
                "Ce numéro est déjà utilisé par un autre type de compte (fan, organisateur, etc.).")

        # CAS B : agent actif
        if existing_user.is_active and existing_user.role == "agent":
            if existing_user.org_name == org_name_actuel:
                raise HTTPException(400, f"{existing_user.full_name} est déjà un agent dans votre équipe.")
            else:
                raise HTTPException(400, "Ce numéro appartient à un agent d'une autre organisation.")

        # CAS C : agent désactivé → réactivation
        if not existing_user.is_active and existing_user.role == "agent":
            existing_user.is_active = True
            existing_user.full_name = agent.full_name
            existing_user.pin_hash = hash_pin(agent.pin_code)
            existing_user.org_name = org_name_actuel
            existing_user.agent_event_id = agent.event_id
            existing_user.failed_login_attempts = 0
            existing_user.locked_until = None
            db.commit()

            log_security_event(db, "agent_reactivated", None, current_user.id,
                               f"Agent {existing_user.full_name} ({existing_user.phone_number}) réactivé pour event {event.id}")

            return {
                "message": f"Agent {existing_user.full_name} réactivé avec succès pour cet événement.",
                "reactivated": True,
            }

        # CAS D : compte désactivé d'un autre type
        if not existing_user.is_active:
            raise HTTPException(400,
                "Ce numéro appartient à un compte désactivé d'un autre type. Contactez le support.")

    # CAS E : numéro nouveau
    new_agent = User(
        phone_number=agent.phone_number,
        full_name=agent.full_name,
        pin_hash=hash_pin(agent.pin_code),
        role="agent",
        org_name=org_name_actuel,
        agent_event_id=agent.event_id,
    )
    db.add(new_agent)
    db.commit()

    log_security_event(db, "agent_created", None, current_user.id,
                       f"Nouvel agent {new_agent.full_name} créé pour event {event.id}")

    return {"message": "Agent créé avec succès", "reactivated": False}


@router.get("/organizer/agents")
def get_agents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    agents = (
        db.query(User)
        .filter(
            User.role == "agent",
            User.org_name == (current_user.org_name or current_user.full_name),
            User.is_active == True,
        )
        .order_by(User.created_at.desc())
        .all()
    )

    # Résolution des événements en UNE requête (anti N+1)
    event_ids = {a.agent_event_id for a in agents if a.agent_event_id}
    events_map = {}
    if event_ids:
        events = db.query(Event).filter(Event.id.in_(event_ids)).all()
        events_map = {e.id: e for e in events}

    return [{
        "id": a.id,
        "full_name": a.full_name,
        "phone_number": a.phone_number,
        "event_name": events_map[a.agent_event_id].title if a.agent_event_id in events_map else "Événement supprimé",
    } for a in agents]


@router.delete("/organizer/agents/{agent_id}")
def delete_agent(agent_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    agent = db.query(User).filter(
        User.id == agent_id,
        User.role == "agent",
        User.org_name == (current_user.org_name or current_user.full_name),
    ).first()
    if not agent:
        raise HTTPException(404, "Agent introuvable")

    db.delete(agent)
    db.commit()
    return {"message": "Agent supprimé avec succès"}
