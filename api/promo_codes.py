"""
Chantier 19.A : Codes promo CRUD organisateur + validation publique.

Convention :
  - L'organisateur gère ses codes (CRUD complet sauf hard delete)
  - Soft-delete via is_active=false pour préserver l'audit promo_usages
  - Route publique /promo-codes/validate pour preview UX sans side-effect
"""
import random
import string
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user, log_security_event

from models.event import Event
from models.promo import PromoCode, PromoUsage
from models.user import User

from schemas.promo import (
    PromoCodeCreate, PromoCodeUpdate, PromoCodeOut,
    PromoValidateIn, PromoValidateOut, GeneratedCodeOut,
)
from utils.promo import find_active_promo, compute_discount
from utils.slugs import event_id_to_slug

router = APIRouter(tags=["promo_codes"])


# ─── Helpers internes ────────────────────────────────────────────

def _assert_organizer(user: User) -> None:
    """Calque api/promotion.py — rôle legacy 'organisation' encore actif."""
    if user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès réservé aux organisateurs")


def _check_event_belongs(db: Session, event_id: int, user: User) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    if event.organizer_id != user.id:
        raise HTTPException(403, "Cet événement ne vous appartient pas")
    return event


def _check_tier_exists(event: Event, tier_name: str) -> None:
    """Vérifie que tier_name existe dans event.ticket_tiers."""
    tiers = event.ticket_tiers or []
    if isinstance(tiers, str):
        # Sécurité : si jsonb stocké en str (cas legacy)
        import json
        try:
            tiers = json.loads(tiers)
        except Exception:
            tiers = []
    names = {t.get("name") for t in tiers if isinstance(t, dict)}
    if tier_name not in names:
        raise HTTPException(400, f"Catégorie '{tier_name}' introuvable dans cet événement")


def _build_share_link(promo: PromoCode, db: Session) -> Optional[str]:
    """Construit le lien partageable si event_id renseigné.

    Le slug est DÉRIVÉ de l'event_id via hashids (utils/slugs.py) — il n'y a
    aucune colonne `slug` en BD (référence fantôme corrigée). L'URL courte
    /e/{slug} est résolue côté frontend via slug_to_event_id.
    """
    if not promo.event_id:
        return None
    try:
        slug = event_id_to_slug(promo.event_id)
        if slug:
            return f"https://goevent.africa/e/{slug}?promo={promo.code}"
    except Exception:
        pass
    return f"https://goevent.africa/event_detail.html?id={promo.event_id}&promo={promo.code}"


def _to_out(promo: PromoCode, db: Session, with_stats: bool = False, event_title: Optional[str] = None) -> PromoCodeOut:
    """Sérialisation centralisée. with_stats=True déclenche 2 queries supplémentaires."""
    confirmed_count = 0
    total_discount = 0.0
    if with_stats:
        row = (
            db.query(
                func.count(PromoUsage.id),
                func.coalesce(func.sum(PromoUsage.discount_fcfa), 0),
            )
            .filter(
                PromoUsage.promo_id == promo.id,
                PromoUsage.status == "confirmed",
            )
            .first()
        )
        confirmed_count = int(row[0] or 0)
        total_discount = float(row[1] or 0)

    return PromoCodeOut(
        id=promo.id,
        code=promo.code,
        discount_type=promo.discount_type,
        discount_value=float(promo.discount_value),
        event_id=promo.event_id,
        event_title=event_title,
        tier_name=promo.tier_name,
        max_uses_global=promo.max_uses_global,
        max_uses_per_user=promo.max_uses_per_user,
        used_count=promo.used_count,
        confirmed_count=confirmed_count,
        total_discount_fcfa=total_discount,
        starts_at=promo.starts_at,
        ends_at=promo.ends_at,
        is_active=promo.is_active,
        created_at=promo.created_at,
        share_link=_build_share_link(promo, db),
    )


# ═════════════════════════════════════════════════════════════════
# ROUTES ORGANISATEUR
# ═════════════════════════════════════════════════════════════════

@router.post("/organizer/promo-codes/generate-code", response_model=GeneratedCodeOut)
def generate_unique_code(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Helper : génère un code aléatoire [A-Z0-9]{8} unique pour cet organisateur.
    Pas de pré-création, juste un suggéré. Retry max 10 fois en cas de collision.
    """
    _assert_organizer(current_user)

    for _ in range(10):
        candidate = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        exists = (
            db.query(PromoCode)
            .filter(
                PromoCode.organizer_id == current_user.id,
                func.lower(PromoCode.code) == candidate.lower(),
            )
            .first()
        )
        if not exists:
            return GeneratedCodeOut(code=candidate)

    raise HTTPException(500, "Impossible de générer un code unique, réessayez")


@router.post("/organizer/promo-codes", response_model=PromoCodeOut)
def create_promo_code(
    data: PromoCodeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Crée un code promo. Code stocké en uppercase, unique par orga."""
    _assert_organizer(current_user)

    # Validation event
    event = None
    if data.event_id is not None:
        event = _check_event_belongs(db, data.event_id, current_user)
        if data.tier_name:
            _check_tier_exists(event, data.tier_name)

    # Validation cohérence dates
    if data.starts_at and data.ends_at and data.starts_at >= data.ends_at:
        raise HTTPException(400, "La date de début doit être antérieure à la date de fin")

    promo = PromoCode(
        organizer_id=current_user.id,
        event_id=data.event_id,
        tier_name=data.tier_name,
        code=data.code,  # déjà uppercase via validator
        discount_type=data.discount_type,
        discount_value=data.discount_value,
        max_uses_global=data.max_uses_global,
        max_uses_per_user=data.max_uses_per_user,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        is_active=data.is_active,
    )
    db.add(promo)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"Le code '{data.code}' existe déjà pour votre organisation")

    db.refresh(promo)

    log_security_event(
        db, "promo_code_created", None, current_user.id,
        f"Code '{promo.code}' ({promo.discount_type} {promo.discount_value})",
    )

    return _to_out(promo, db, with_stats=False,
                   event_title=event.title if event else None)


@router.get("/organizer/promo-codes", response_model=List[PromoCodeOut])
def list_my_promo_codes(
    event_id: Optional[int] = Query(None),
    active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Liste les codes de l'organisateur. Filtres optionnels event_id et active.
    Résolution event_title sans N+1.
    """
    _assert_organizer(current_user)

    q = db.query(PromoCode).filter(PromoCode.organizer_id == current_user.id)
    if event_id is not None:
        q = q.filter(PromoCode.event_id == event_id)
    if active is not None:
        q = q.filter(PromoCode.is_active == active)

    promos = q.order_by(PromoCode.created_at.desc()).limit(200).all()

    # N+1 resolution (calque promotion.py)
    event_ids = list({p.event_id for p in promos if p.event_id})
    events_map = {
        e.id: e.title
        for e in db.query(Event.id, Event.title).filter(Event.id.in_(event_ids)).all()
    } if event_ids else {}

    return [
        _to_out(p, db, with_stats=False,
                event_title=events_map.get(p.event_id) if p.event_id else None)
        for p in promos
    ]


@router.get("/organizer/promo-codes/{promo_id}", response_model=PromoCodeOut)
def get_promo_code_detail(
    promo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Détail d'un code AVEC stats (confirmed_count, total_discount_fcfa)."""
    _assert_organizer(current_user)

    promo = db.query(PromoCode).filter(PromoCode.id == promo_id).first()
    if not promo:
        raise HTTPException(404, "Code promo introuvable")
    if promo.organizer_id != current_user.id:
        raise HTTPException(403, "Ce code ne vous appartient pas")

    event_title = None
    if promo.event_id:
        ev = db.query(Event.title).filter(Event.id == promo.event_id).first()
        event_title = ev.title if ev else None

    return _to_out(promo, db, with_stats=True, event_title=event_title)


@router.patch("/organizer/promo-codes/{promo_id}", response_model=PromoCodeOut)
def update_promo_code(
    promo_id: int,
    data: PromoCodeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Modification d'un code.
    Si used_count > 0 : seuls is_active et ends_at modifiables (audit).
    Tout autre champ → 409.
    """
    _assert_organizer(current_user)

    promo = db.query(PromoCode).filter(PromoCode.id == promo_id).with_for_update().first()
    if not promo:
        raise HTTPException(404, "Code promo introuvable")
    if promo.organizer_id != current_user.id:
        raise HTTPException(403, "Ce code ne vous appartient pas")

    payload = data.model_dump(exclude_unset=True)

    if promo.used_count > 0:
        forbidden = set(payload.keys()) - {"is_active", "ends_at"}
        if forbidden:
            raise HTTPException(
                409,
                f"Ce code a déjà été utilisé. Seuls 'is_active' et 'ends_at' restent modifiables (interdits : {', '.join(sorted(forbidden))})",
            )

    if "starts_at" in payload and "ends_at" in payload:
        if payload["starts_at"] and payload["ends_at"] and payload["starts_at"] >= payload["ends_at"]:
            raise HTTPException(400, "La date de début doit être antérieure à la date de fin")

    changes = []
    for k, v in payload.items():
        old = getattr(promo, k, None)
        if old != v:
            changes.append(f"{k}: {old}→{v}")
            setattr(promo, k, v)

    db.commit()
    db.refresh(promo)

    if changes:
        log_security_event(
            db, "promo_code_updated", None, current_user.id,
            f"Code #{promo.id} : {'; '.join(changes)}",
        )

    event_title = None
    if promo.event_id:
        ev = db.query(Event.title).filter(Event.id == promo.event_id).first()
        event_title = ev.title if ev else None

    return _to_out(promo, db, with_stats=True, event_title=event_title)


@router.delete("/organizer/promo-codes/{promo_id}")
def delete_promo_code(
    promo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Soft-delete : passe is_active=false. Jamais de hard delete pour préserver
    l'historique des promo_usages liées (audit + stats).
    """
    _assert_organizer(current_user)

    promo = db.query(PromoCode).filter(PromoCode.id == promo_id).first()
    if not promo:
        raise HTTPException(404, "Code promo introuvable")
    if promo.organizer_id != current_user.id:
        raise HTTPException(403, "Ce code ne vous appartient pas")

    if not promo.is_active:
        return {"message": "Ce code est déjà désactivé"}

    promo.is_active = False
    db.commit()

    log_security_event(
        db, "promo_code_deactivated", None, current_user.id,
        f"Code '{promo.code}' (#{promo.id}) désactivé",
    )

    return {"message": f"Code '{promo.code}' désactivé"}


# ═════════════════════════════════════════════════════════════════
# ROUTE PUBLIQUE : VALIDATION PREVIEW
# ═════════════════════════════════════════════════════════════════

@router.post("/promo-codes/validate", response_model=PromoValidateOut)
def validate_promo_code(
    data: PromoValidateIn,
    db: Session = Depends(get_db),
):
    """
    Preview UX côté fan : valide un code et retourne le nouveau prix.
    AUCUN side-effect (pas d'incrément used_count, pas de PromoUsage créée).
    Le vrai check + verrou se fait à l'initiate-ngser / reserve-cash.

    Note : on ne vérifie PAS max_uses_per_user ici (pas d'auth requise pour la preview).
    L'erreur "Déjà utilisé" remontera au moment du paiement avec un message clair.
    """
    event = db.query(Event).filter(Event.id == data.event_id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    # Recherche du code via organizer_id de l'event
    try:
        promo = find_active_promo(
            db,
            organizer_id=event.organizer_id,
            code=data.code,
            event_id=data.event_id,
            tier_name=data.tier_name,
        )
    except HTTPException as e:
        return PromoValidateOut(
            valid=False,
            code=data.code.strip().upper(),
            message=e.detail if isinstance(e.detail, str) else "Code invalide",
        )

    # Check épuisement global (sans verrou, preview uniquement)
    if promo.max_uses_global is not None and promo.used_count >= promo.max_uses_global:
        return PromoValidateOut(
            valid=False,
            code=promo.code,
            message="Ce code promo est épuisé",
        )

    # Calcul prix
    prix_base = float(event.price)
    if data.tier_name and event.ticket_tiers:
        tiers = event.ticket_tiers
        if isinstance(tiers, str):
            import json
            try:
                tiers = json.loads(tiers)
            except Exception:
                tiers = []
        if isinstance(tiers, list):
            tier = next((t for t in tiers if isinstance(t, dict) and t.get("name") == data.tier_name), None)
            if tier and tier.get("price") is not None:
                prix_base = float(tier["price"])

    discount = compute_discount(promo, prix_base)
    new_price = int(max(0, prix_base - discount))

    return PromoValidateOut(
        valid=True,
        code=promo.code,
        message=f"Code appliqué : -{discount:,} FCFA".replace(",", " "),
        discount_fcfa=discount,
        original_price=int(prix_base),
        new_price=new_price,
    )
