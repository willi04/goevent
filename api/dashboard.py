"""
api/dashboard.py
Tableau de bord du fan : ses billets (payés, cash, réservés), total dépensé,
événements à venir.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.ticket import Ticket
from models.user import User

router = APIRouter()


@router.get("/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tickets = db.query(Ticket).filter(Ticket.user_id == current_user.id).all()

    valid_statuses = ["paye", "paye_cash", "reservation_cash"]
    valid_tickets = [t for t in tickets if t.payment_status in valid_statuses]

    paid_tickets = [t for t in tickets if t.payment_status in ["paye", "paye_cash"]]
    total_spend = sum((t.cash_amount or t.event.price) for t in paid_tickets if t.event)

    upcoming = [
        t for t in valid_tickets
        if t.event and not t.is_used and t.event.event_date >= datetime.utcnow()
    ]

    return {
        "total_tickets": len(valid_tickets),
        "upcoming_events": len(upcoming),
        "total_spend": total_spend,
        "tickets": [{
            "ticket_id": t.id,
            "event_title": t.event.title if t.event else "",
            "event_date": t.event.event_date if t.event else None,
            "event_location": t.event.location if t.event else "",
            "price": (t.cash_amount or (t.event.price if t.event else 0)),
            "is_used": t.is_used,
            "qr_hash": t.qr_hash,
            "payment_status": t.payment_status,
            "payment_method": t.payment_method,
        } for t in valid_tickets[:10]],
    }
