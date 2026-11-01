"""
api/admin_events.py — Back-office « Événements » (lot 5).

Événements PUBLICS uniquement (les privés se gèrent dans Invitations).
Liste paginée avec statut calculé, compteurs et indicateurs ; désactivation
motivée et journalisée (admin), réactivation ; e-mail facultatif à l'organisateur.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session, joinedload

from core.database import get_db
from core.event_query import STATUSES, like, status_filter, status_of, watch_filter, window
from core.limiter import limiter, user_key
from core.permissions import get_current_admin, get_current_admin_or_staff
from core.security import log_security_event
from models.event import Event
from models.payment import Payment
from models.promotion import BoostOrder
from models.ticket import Ticket
from models.user import User
from schemas.event import EventDeactivateIn
from utils.slugs import event_id_to_slug

router = APIRouter()

PAID = ("paye", "paye_cash")


def _public(db: Session):
    return db.query(Event).filter(Event.visibility == "public")


def _revenue(db: Session, event_ids, since: datetime = None) -> dict:
    """Recettes brutes billets (prix des billets, hors frais acheteur), par événement."""
    if not event_ids and since is None:
        return {}
    online = (db.query(Ticket.event_id, func.coalesce(func.sum(Payment.base_price), 0))
              .join(Payment, and_(Payment.ticket_id == Ticket.id, Payment.status == "completed"))
              .filter(Ticket.payment_status.in_(PAID)))
    cash = (db.query(Ticket.event_id, func.coalesce(func.sum(Ticket.cash_amount), 0))
            .filter(Ticket.payment_status == "paye_cash"))
    if event_ids is not None:
        online = online.filter(Ticket.event_id.in_(event_ids))
        cash = cash.filter(Ticket.event_id.in_(event_ids))
    if since is not None:
        online = online.filter(Ticket.purchased_at >= since)
        cash = cash.filter(Ticket.purchased_at >= since)
    out = {}
    for rows in (online.group_by(Ticket.event_id).all(), cash.group_by(Ticket.event_id).all()):
        for eid, amount in rows:
            out[eid] = out.get(eid, 0) + int(round(float(amount or 0)))
    return out


def _item(e: Event, now: datetime, revenue: dict, boosted: set) -> dict:
    total = e.total_seats or 0
    sold = e.seats_sold or 0
    org = e.organizer_user
    return {
        "id": e.id,
        "slug": event_id_to_slug(e.id),
        "title": e.title,
        "category": e.category,
        "organizer": (org.org_name or org.full_name) if org else "Organisateur supprimé",
        "organizer_id": e.organizer_id,
        "event_date": e.event_date,
        "location": e.location,
        "price": e.price,
        "seats_sold": sold,
        "total_seats": total,
        "fill_pct": round(sold * 100 / total) if total > 0 else 0,
        "status": status_of(e, now),
        "is_active": bool(e.is_active),
        "is_boosted": e.id in boosted,
        "revenue_fcfa": revenue.get(e.id, 0),
        "deactivated_reason": e.deactivated_reason,
        "deactivated_at": e.deactivated_at,
    }


@router.get("/admin/events")
@limiter.limit("120/minute", key_func=user_key)
def admin_list_events(
    request: Request,
    page: Optional[int] = Query(None, ge=1, le=1000),
    limit: int = Query(20, ge=1, le=50),
    status: str = Query("tous", pattern="^(tous|avenir|encours|complet|passe|desactive|surveiller)$"),
    q: Optional[str] = Query(None, max_length=80),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_staff),
):
    """Sans `page` : ancien format (tableau léger) pour l'interface actuelle.
    Avec `page` : {total, page, limit, counts, kpis, items}."""
    now = datetime.utcnow()
    base = _public(db).options(joinedload(Event.organizer_user))

    if page is None:   # compatibilité admin.html avant l'étape 2 (aucune image base64 renvoyée)
        rows = base.order_by(Event.event_date.desc()).limit(500).all()
        return [{"id": e.id, "title": e.title, "category": e.category,
                 "organizer": (e.organizer_user.org_name or e.organizer_user.full_name) if e.organizer_user else None,
                 "event_date": e.event_date, "seats_sold": e.seats_sold or 0, "total_seats": e.total_seats or 0,
                 "is_active": bool(e.is_active), "status": status_of(e, now)} for e in rows]

    def searched(qry):
        if q and q.strip():
            pat = like(q.strip())
            qry = qry.outerjoin(User, User.id == Event.organizer_id).filter(
                Event.title.ilike(pat, escape="\\") | Event.location.ilike(pat, escape="\\")
                | User.org_name.ilike(pat, escape="\\") | User.full_name.ilike(pat, escape="\\"))
        return qry

    # Compteurs par statut (même recherche) : une seule requête
    sums = [func.sum(case((status_filter(s, now), 1), else_=0)) for s in STATUSES]
    sums.append(func.sum(case((watch_filter(now), 1), else_=0)))
    row = searched(_public(db)).with_entities(*sums).one()
    counts = {s: int(row[i] or 0) for i, s in enumerate(STATUSES)}
    counts["surveiller"] = int(row[-1] or 0)
    counts["tous"] = sum(counts[s] for s in STATUSES)

    query = searched(base)
    if status == "surveiller":
        query = query.filter(watch_filter(now)).order_by(Event.event_date.asc())
    elif status in ("avenir", "complet", "encours"):
        query = query.filter(status_filter(status, now)).order_by(Event.event_date.asc())
    elif status in ("passe", "desactive"):
        query = query.filter(status_filter(status, now)).order_by(Event.event_date.desc())
    else:
        query = query.order_by(Event.event_date.desc())

    total = counts["surveiller"] if status == "surveiller" else counts[status]
    events = query.offset((page - 1) * limit).limit(limit).all()
    ids = [e.id for e in events]
    revenue = _revenue(db, ids) if ids else {}
    boosted = {r[0] for r in db.query(BoostOrder.event_id).filter(
        BoostOrder.event_id.in_(ids), BoostOrder.status == "actif", BoostOrder.ends_at > now).all()} if ids else set()

    # Indicateurs (indépendants de la recherche)
    since = now - timedelta(days=30)
    upcoming = _public(db).filter(Event.is_active.is_(True), Event.event_date > now)
    we = window("weekend", now)
    kpis = {
        "upcoming": upcoming.count(),
        "upcoming_weekend": upcoming.filter(Event.event_date >= we[0], Event.event_date < we[1]).count(),
        "upcoming_seats": int(upcoming.with_entities(func.coalesce(func.sum(Event.total_seats), 0)).scalar() or 0),
        "tickets_30d": (db.query(func.count(Ticket.id)).join(Event, Event.id == Ticket.event_id)
                        .filter(Event.visibility == "public", Ticket.payment_status.in_(PAID),
                                Ticket.purchased_at >= since).scalar() or 0),
        "revenue_30d": sum(_revenue(db, None, since).values()),
        "watch": _public(db).filter(watch_filter(now)).count(),
    }
    return {"total": total, "page": page, "limit": limit, "counts": counts, "kpis": kpis,
            "items": [_item(e, now, revenue, boosted) for e in events]}


def _load_public(db: Session, event_id: int, lock: bool = False) -> Event:
    q = db.query(Event).filter(Event.id == event_id)
    if lock:
        q = q.with_for_update()
    e = q.first()
    if not e:
        raise HTTPException(404, "Événement introuvable")
    if e.visibility != "public":
        raise HTTPException(400, "Événement privé : à gérer depuis Invitations.")
    return e


def _notify_organizer(email: str, name: str, title: str, reason: str) -> None:
    from core.email import envoyer_mail_desactivation
    envoyer_mail_desactivation(email, name, title, reason)


@router.post("/admin/events/{event_id}/deactivate")
@limiter.limit("30/minute", key_func=user_key)
def deactivate_event(request: Request, event_id: int, data: EventDeactivateIn, background: BackgroundTasks,
                     db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    e = _load_public(db, event_id, lock=True)
    if not e.is_active:
        raise HTTPException(409, "Événement déjà désactivé.")
    sold = db.query(func.count(Ticket.id)).filter(Ticket.event_id == e.id, Ticket.payment_status.in_(PAID)).scalar() or 0
    amount = _revenue(db, [e.id]).get(e.id, 0)
    e.is_active = False
    e.deactivated_reason = data.reason
    e.deactivated_at = datetime.utcnow()
    e.deactivated_by = admin.id
    db.commit()
    org = db.get(User, e.organizer_id) if e.organizer_id else None
    notified = bool(data.notify_organizer and org and org.email)
    log_security_event(db, "event_deactivated", request, admin.id,
                       "#%d %s · %d billet(s) payé(s), %d FCFA · motif : %s%s" % (
                           e.id, (e.title or "")[:80], sold, amount, data.reason,
                           " · organisateur prévenu" if notified else ""))
    if notified:
        background.add_task(_notify_organizer, org.email, org.full_name, e.title, data.reason)
    return {"ok": True, "status": "desactive", "tickets_paid": sold, "revenue_fcfa": amount,
            "organizer_notified": notified,
            "message": ("%d billet(s) payé(s) restent valables. Pour annuler et rembourser, passer par Annulations." % sold)
            if sold else "Événement retiré du site."}


@router.post("/admin/events/{event_id}/reactivate")
@limiter.limit("30/minute", key_func=user_key)
def reactivate_event(request: Request, event_id: int, db: Session = Depends(get_db),
                     admin: User = Depends(get_current_admin)):
    e = _load_public(db, event_id, lock=True)
    if e.is_active:
        raise HTTPException(409, "Événement déjà actif.")
    old = e.deactivated_reason
    e.is_active = True
    e.deactivated_reason = e.deactivated_at = e.deactivated_by = None
    db.commit()
    log_security_event(db, "event_reactivated", request, admin.id,
                       "#%d %s · ancien motif : %s" % (e.id, (e.title or "")[:80], old or "—"))
    return {"ok": True, "status": status_of(e, datetime.utcnow())}


@router.put("/admin/events/{event_id}")
@limiter.limit("30/minute")
def admin_update_event(request: Request, event_id: int, data: dict, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_admin)):
    """Ancienne interface (avant l'étape 2) : bascule is_active, désormais journalisée."""
    e = _load_public(db, event_id, lock=True)
    if "is_active" in data and bool(data["is_active"]) != bool(e.is_active):
        e.is_active = bool(data["is_active"])
        if e.is_active:
            e.deactivated_reason = e.deactivated_at = e.deactivated_by = None
        else:
            e.deactivated_reason = "Sans motif (ancienne interface)"
            e.deactivated_at, e.deactivated_by = datetime.utcnow(), current_user.id
        db.commit()
        log_security_event(db, "event_reactivated" if e.is_active else "event_deactivated", request, current_user.id,
                           "#%d %s · ancienne interface" % (e.id, (e.title or "")[:80]))
    return {"message": "Événement mis à jour"}
