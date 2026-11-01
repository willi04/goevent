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
from core.event_categories import CATEGORIES, canonical, variants
from core.event_query import like, window
from utils.serializers import _event_dict

router = APIRouter()


@router.get("/events")
def list_events(
    # Bornes (lot 5) : page 0 → OFFSET négatif (500), limit libre → table entière.
    page: int = Query(1, ge=1, le=500),
    limit: int = Query(6, ge=1, le=24),
    sort_by: Optional[str] = "date_asc",
    q: Optional[str] = Query(None, max_length=80),
    categories: Optional[str] = Query(None, max_length=300),
    location: Optional[str] = Query(None, max_length=80),
    max_price: Optional[float] = None,
    upcoming_only: bool = False,
    include_past_days: int = Query(0, ge=0, le=3650),
    when: Optional[str] = Query(None, pattern="^(today|weekend|month)$"),
    free_only: bool = False,
    # Page Événements (lot 5) : « À la une » = boostés visibles seulement ; la liste
    # suit l'ordre choisi sans remonter les boostés (ils sont déjà dans « À la une »).
    boosted_only: bool = False,
    boost_first: bool = True,
    db: Session = Depends(get_db),
):
    from models.promotion import BoostOrder
    from sqlalchemy import case
    import random
    import time

    # Événements privés (invitations) : jamais listés ni recherchables
    query = db.query(Event).filter(Event.is_active == True, Event.visibility == "public")

    # Filtre temporel
    if upcoming_only:
        query = query.filter(Event.event_date >= datetime.utcnow())
    elif include_past_days > 0:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=include_past_days)
        query = query.filter(Event.event_date >= cutoff)

    span = window(when) if when else None
    if span:
        query = query.filter(Event.event_date >= max(span[0], datetime.utcnow()), Event.event_date < span[1])

    if q and q.strip():
        pat = like(q.strip())
        query = query.filter(or_(
            Event.title.ilike(pat, escape="\\"),
            Event.location.ilike(pat, escape="\\"),
            Event.description.ilike(pat, escape="\\"),
        ))
    if categories:
        # Libellés canoniques + variantes sans accent encore stockées (avant rattrapage)
        wanted = set()
        for c in categories.split(","):
            label = canonical(c)
            if label:
                wanted |= variants(label)
        if not wanted:
            return {"total": 0, "page": page, "limit": limit, "events": []}
        query = query.filter(Event.category.in_(wanted))
    if location and location.strip() and location != "Tous les lieux":
        query = query.filter(Event.location.ilike(like(location.strip()), escape="\\"))
    if max_price is not None:
        query = query.filter(Event.price <= max_price)
    if free_only:
        query = query.filter(Event.price <= 0)

    # Tri secondaire (utilisateur)
    if sort_by == "price_asc":
        order_clauses = [Event.price.asc()]
    elif sort_by == "price_desc":
        order_clauses = [Event.price.desc()]
    elif sort_by == "date_desc":
        order_clauses = [Event.event_date.desc()]
    else:
        order_clauses = [Event.event_date.asc()]

    # ─── Chantier 17 : sélection des boostés visibles (rotation 5 min) ──
    # On limite à MAX_BOOSTS_VISIBLE boostés visibles simultanément, peu importe
    # combien sont actifs en BD. Rotation déterministe : même tirage pendant
    # une fenêtre de 5 min serveur, pour cohérence cross-pages.
    MAX_BOOSTS_VISIBLE = 6
    ROTATION_BUCKET_SECONDS = 300  # 5 minutes

    now = datetime.utcnow()
    active_boost_event_ids = [
        row[0]
        for row in db.query(BoostOrder.event_id)
        .filter(
            BoostOrder.status == "actif",
            BoostOrder.ends_at > now,
        )
        .all()
    ]

    # Tirage déterministe basé sur le bucket de 5 min
    if len(active_boost_event_ids) > MAX_BOOSTS_VISIBLE:
        bucket = int(time.time()) // ROTATION_BUCKET_SECONDS
        rng = random.Random(bucket)
        visible_boost_ids = set(rng.sample(active_boost_event_ids, MAX_BOOSTS_VISIBLE))
    else:
        visible_boost_ids = set(active_boost_event_ids)

    if boosted_only:
        if not visible_boost_ids:
            return {"total": 0, "page": page, "limit": limit, "events": []}
        query = query.filter(Event.id.in_(visible_boost_ids))

    # ─── Tri SQL : visibles_boostés d'abord, puis tri standard ──────────
    if visible_boost_ids and boost_first:
        boost_priority = case(
            (Event.id.in_(visible_boost_ids), 0),
            else_=1,
        )
        query = query.order_by(boost_priority.asc(), *order_clauses)
    else:
        query = query.order_by(*order_clauses)

    total_items = query.count()
    skip = (page - 1) * limit
    events = query.offset(skip).limit(limit).all()

    # Le flag is_boosted est TRUE uniquement pour les IDs visibles dans ce bucket.
    # Un boost actif mais "en rotation cachée" ce bucket-ci n'apparaît PAS comme boosté.
    return {
        "total": total_items,
        "page": page,
        "limit": limit,
        "events": [_event_dict(e, is_boosted=(e.id in visible_boost_ids)) for e in events],
    }
    

@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    # Catégories des événements PUBLICS à venir (jamais « Privé »), variantes
    # fusionnées sur le libellé canonique, triées par nombre d'événements.
    cats = (
        db.query(Event.category, func.count(Event.id).label("count"))
        .filter(Event.is_active == True, Event.visibility == "public",
                Event.event_date >= datetime.utcnow())
        .group_by(Event.category)
        .all()
    )
    merged = {}
    for c, n in cats:
        label = canonical(c) or "Autre"
        merged[label] = merged.get(label, 0) + n
    return [{"category": c, "count": n} for c, n in sorted(merged.items(), key=lambda x: (-x[1], x[0]))]


@router.get("/categories/all")
def all_categories():
    """Liste fermée (formulaire organisateur, filtres) — aucune donnée en base."""
    return list(CATEGORIES)


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


@router.get("/events/{event_id}/related")
def related_events(event_id: int, limit: int = Query(6, ge=1, le=12), db: Session = Depends(get_db)):
    """« À découvrir aussi » (fiche, lot 5) : à venir uniquement, hors événement courant.
    Ordre : même organisateur → mis en avant (boost) → même catégorie → les plus proches."""
    from sqlalchemy.orm import joinedload
    from models.promotion import BoostOrder
    ev = db.query(Event).filter(Event.id == event_id, Event.visibility == "public").first()
    if not ev:
        raise HTTPException(404, "Événement introuvable")
    now = datetime.utcnow()
    base = (db.query(Event).options(joinedload(Event.organizer_user))
            .filter(Event.visibility == "public", Event.is_active == True,
                    Event.event_date >= now, Event.id != ev.id))
    boosted = {r[0] for r in db.query(BoostOrder.event_id).filter(
        BoostOrder.status == "actif", BoostOrder.ends_at > now).all()}
    out, seen = [], set()

    def take(query, reason):
        for e in query.order_by(Event.event_date.asc()).limit(limit).all():
            if len(out) < limit and e.id not in seen:
                seen.add(e.id)
                out.append((e, reason))

    if ev.organizer_id:
        take(base.filter(Event.organizer_id == ev.organizer_id), "organizer")
    if boosted:
        take(base.filter(Event.id.in_(boosted)), "boost")
    label = canonical(ev.category)
    if label:
        take(base.filter(Event.category.in_(variants(label))), "category")
    take(base, "soon")
    return [dict(_event_dict(e, is_boosted=e.id in boosted), related_reason=r) for e, r in out]


@router.get("/events/by-slug/{slug}")
def get_event_by_slug(slug: str, db: Session = Depends(get_db)):
    from utils.slugs import slug_to_event_id
    event_id = slug_to_event_id(slug)
    if event_id is None:
        raise HTTPException(404, "Lien invalide")
    event = db.query(Event).filter(Event.id == event_id, Event.is_active == True,
                                   Event.visibility == "public").first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    return _event_dict(event)


@router.get("/events/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    # Un événement privé n'est pas lisible par énumération d'id (titre, lieu, date d'un mariage…)
    e = db.query(Event).filter(Event.id == event_id, Event.visibility == "public").first()
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
    changes = data.model_dump(exclude_none=True)
    if "total_seats" in changes and changes["total_seats"] < (event.seats_sold or 0):
        raise HTTPException(409, f"Impossible : {event.seats_sold} places déjà vendues/réservées.")
    for k, v in changes.items():
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
    if event.is_active:
        event.is_active = False
        event.deactivated_reason = "Retiré par l'organisateur"
        event.deactivated_at, event.deactivated_by = datetime.utcnow(), current_user.id
    db.commit()
    return {"message": "Événement supprimé"}
