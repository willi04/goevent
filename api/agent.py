"""
api/agent.py
Routeur agent : événement assigné (pour l'écran de scan) et autodestruction
du compte agent (désactivation si historique cash, suppression sinon).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user, log_security_event
from models.event import Event
from models.ticket import Ticket
from models.user import User

router = APIRouter()


@router.get("/agent/current-event")
def get_agent_event(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role == "agent" and current_user.agent_event_id:
        event = db.query(Event).filter(Event.id == current_user.agent_event_id).first()
        if event:
            return {"event_name": event.title}

    if current_user.role in ("organizer", "organisation"):
        event = (
            db.query(Event)
            .filter(Event.organizer_id == current_user.id)
            .order_by(Event.id.desc())
            .first()
        )
        if event:
            return {"event_name": event.title}

    return {"event_name": "Contrôle des billets"}


@router.delete("/agent/delete-account")
def delete_agent_account(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Désactive le compte agent (déconnexion permanente).
    On ne supprime PAS le compte s'il a un historique cash, pour préserver la traçabilité.
    """
    if current_user.role != "agent":
        raise HTTPException(status_code=403, detail="Seuls les agents peuvent être supprimés ainsi.")

    has_cash_history = db.query(Ticket).filter(
        Ticket.cash_collected_by == current_user.id
    ).first() is not None

    if has_cash_history:
        current_user.is_active = False
        current_user.agent_event_id = None
        db.commit()
        log_security_event(db, "agent_deactivated", None, current_user.id,
                           f"Agent {current_user.full_name} désactivé (historique préservé)")
        return {"message": "Compte agent désactivé. Historique conservé pour la traçabilité."}
    else:
        db.delete(current_user)
        db.commit()
        return {"message": "Compte agent supprimé définitivement."}
