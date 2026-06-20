"""
api/events.py
Routeur événements : CRUD, listing public paginé/filtré, catégories,
mes événements (organisateur), récupération par slug.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.event import Event
from models.user import User
from schemas.event import EventCreate, EventUpdate
from utils.serializers import _event_dict

router = APIRouter()


@router.get("/events")
def list_events(
    page: int = 1,
    limit: int = 6,
    sort_by: Optional[str] = "date_asc",
    q: Optional[str] = None,
    categories: Optional[str] = None,
    location: Optional[str] = None,
    max_price: Optional[float] = None,
    upcoming_only: bool = False,
    include_past_days: int = Query(0, ge=0, le=3650),
    db: Session = Depends(get_db),
):
    query = db.query(Event).filter(Event.is_active == True)

    # Filtre temporel
    # Priorité 1 : upcoming_only=true → seuls les futurs (compat existante index.html)
    # Priorité 2 : include_past_days > 0 → futurs + N derniers jours
    # Sinon (défaut) : tout est retourné (compat historique)
    if upcoming_only:
        query = query.filter(Event.event_date >= datetime.utcnow())
    elif include_past_days > 0:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=include_past_days)
        query = query.filter(Event.event_date >= cutoff)

    if q:
        query = query.filter(or_(
            Event.title.ilike(f"%{q}%"),
            Event.location.ilike(f"%{q}%"),
            Event.description.ilike(f"%{q}%"),
        ))
    if categories:
        cat_list = categories.split(",")
        query = query.filter(Event.category.in_(cat_list))
    if location and location != "Tous les lieux":
        query = query.filter(Event.location == location)
    if max_price is not None:
        query = query.filter(Event.price <= max_price)

    if sort_by == "price_asc":
        query = query.order_by(Event.price.asc())
    elif sort_by == "price_desc":
        query = query.order_by(Event.price.desc())
    elif sort_by == "date_desc":
        query = query.order_by(Event.event_date.desc())
    else:
        query = query.order_by(Event.event_date.asc())

    total_items = query.count()
    skip = (page - 1) * limit
    events = query.offset(skip).limit(limit).all()

    return {
        "total": total_items,
        "page": page,
        "limit": limit,
        "events": [_event_dict(e) for e in events],
    }

@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    cats = (
        db.query(Event.category, func.count(Event.id).label("count"))
        .filter(Event.is_active == True)
        .group_by(Event.category)
        .all()
    )
    return [{"category": c, "count": n} for c, n in cats]


@router.get("/my-events")
def my_events(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role not in ("artiste", "organizer", "agent"):
        raise HTTPException(403, "Réservé aux organisateurs")
    events = (
        db.query(Event)
        .filter(Event.organizer_id == current_user.id)
        .order_by(Event.event_date.desc())
        .all()
    )
    return [{
        **_event_dict(e),
        "revenue": e.seats_sold * e.price,
        "fill_rate": round(e.seats_sold / e.total_seats * 100, 1) if e.total_seats else 0,
    } for e in events]


@router.get("/events/by-slug/{slug}")
def get_event_by_slug(slug: str, db: Session = Depends(get_db)):
    from utils.slugs import slug_to_event_id
    event_id = slug_to_event_id(slug)
    if event_id is None:
        raise HTTPException(404, "Lien invalide")
    event = db.query(Event).filter(Event.id == event_id, Event.is_active == True).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    return _event_dict(event)


@router.get("/events/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    e = db.query(Event).filter(Event.id == event_id).first()
    if not e:
        raise HTTPException(404, "Événement introuvable")
    return _event_dict(e)


@router.post("/events")
def create_event(
    data: EventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Réservé aux organisateurs")
    event = Event(**data.model_dump(), organizer_id=current_user.id)
    db.add(event)
    db.commit()
    db.refresh(event)
    return {"message": "Événement créé", "event_id": event.id, "event": _event_dict(event)}


@router.put("/events/{event_id}")
def update_event(
    event_id: int,
    data: EventUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = db.query(Event).filter(
        Event.id == event_id, Event.organizer_id == current_user.id
    ).first()
    if not event:
        raise HTTPException(404, "Événement introuvable ou non autorisé")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(event, k, v)
    db.commit()
    return {"message": "Événement mis à jour"}


@router.delete("/events/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = db.query(Event).filter(
        Event.id == event_id, Event.organizer_id == current_user.id
    ).first()
    if not event:
        raise HTTPException(404, "Événement introuvable ou non autorisé")
    event.is_active = False
    db.commit()
    return {"message": "Événement supprimé"}
