"""
Chantier 17 : Routes Régie publicitaire forfaitaire.
- Boost organisateur (forfait 10k/35k/130k)
- Pack Facebook Ads (min 35k + 30% marge)
"""

import uuid
import re
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, Field

from core.database import get_db
from core.security import get_current_user, log_security_event
from core.permissions import require_staff_function, get_current_admin_or_staff
from core.ngser import create_payment_session
from core.config import BOOST_PACKAGES, FB_PACK_MIN_BUDGET, FB_PACK_MARGIN_RATE

from models.user import User
from models.event import Event
from models.promotion import BoostOrder, FbAdPackage

from schemas.promotion import (
    BoostInitiateIn, BoostInitiateOut, BoostOrderOut,
    FbPackRequestIn, FbPackRequestOut, FbPackOut,
    FbPackAdminUpdateIn,
)

router = APIRouter(tags=["promotion"])


# ─── Helpers internes ────────────────────────────────────────────────

def _slugify_event_for_utm(title: str) -> str:
    """Slug court pour UTM (max 30 chars, ASCII only)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
    return s[:30] or "event"


def _event_has_active_boost(db: Session, event_id: int) -> bool:
    """Q1 actée : 1 seul boost actif max par event."""
    now = datetime.utcnow()
    return (
        db.query(BoostOrder)
        .filter(
            BoostOrder.event_id == event_id,
            BoostOrder.status == "actif",
            BoostOrder.ends_at > now,
        )
        .first()
        is not None
    )


def _check_event_ownership(db: Session, event_id: int, user: User) -> Event:
    """Garde : l'event existe et appartient bien à l'orga."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    if event.organizer_id != user.id:
        raise HTTPException(403, "Cet événement ne vous appartient pas")
    if not event.is_active:
        raise HTTPException(400, "Événement inactif ou annulé")
    # Event doit être à venir (pas de promo sur du passé)
    if event.event_date and event.event_date < datetime.utcnow():
        raise HTTPException(400, "L'événement est déjà passé")
    return event


# ═════════════════════════════════════════════════════════════════════
# ROUTES ORGANISATEUR
# ═════════════════════════════════════════════════════════════════════

@router.post("/organizer/boost/initiate", response_model=BoostInitiateOut)
async def initiate_boost(
    data: BoostInitiateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    L'organisateur lance un boost forfaitaire.
    Crée un BoostOrder en 'en_attente_om' + init paiement NGSER.
    Activation via webhook (process_ngser_response → branche BST).
    """
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès réservé aux organisateurs")

    # Vérifications event
    event = _check_event_ownership(db, data.event_id, current_user)

    # Q1 : 1 boost actif max
    if _event_has_active_boost(db, data.event_id):
        raise HTTPException(409, "Cet événement a déjà un boost actif. Attendez son expiration.")

    # Package
    pkg = BOOST_PACKAGES.get(data.package_code)
    if not pkg:
        raise HTTPException(400, "Package invalide")

    # Génération order_id avec préfixe BST-
    order_id = f"BST-{uuid.uuid4().hex[:16].upper()}"

    # Création BoostOrder
    boost = BoostOrder(
        organizer_id=current_user.id,
        event_id=event.id,
        boost_type="forfait",
        package_code=data.package_code,
        duration_days=pkg["duration_days"],
        amount_fcfa=pkg["amount_fcfa"],
        status="en_attente_om",
        payment_ref=order_id,
    )
    db.add(boost)
    db.flush()  # pour avoir boost.id

    # Init paiement NGSER
    try:
        status_code, response_data = await create_payment_session(
            order_id=order_id,
            amount=pkg["amount_fcfa"],
        )
    except Exception as e:
        db.delete(boost)
        db.commit()
        print(f"❌ Erreur réseau NGSER (Boost) : {str(e)}")
        raise HTTPException(503, "Le service de paiement est temporairement indisponible.")

    if status_code != 200 or "payment_url" not in response_data:
        db.delete(boost)
        db.commit()
        print(f"❌ NGSER init Boost : HTTP {status_code} | {response_data}")
        raise HTTPException(400, "Impossible d'initialiser le paiement Orange Money.")

    payment_url = response_data["payment_url"]

    db.commit()

    log_security_event(
        db, "boost_initiated", None, current_user.id,
        f"Boost {data.package_code} event {event.id} - {pkg['amount_fcfa']} FCFA",
    )

    return BoostInitiateOut(
        payment_url=payment_url,
        payment_ref=order_id,
        amount_fcfa=pkg["amount_fcfa"],
        boost_order_id=boost.id,
    )


@router.get("/organizer/boost/my", response_model=List[BoostOrderOut])
def list_my_boosts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Liste des boosts de l'organisateur (tous statuts, plus récents d'abord)."""
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès réservé aux organisateurs")

    boosts = (
        db.query(BoostOrder)
        .filter(BoostOrder.organizer_id == current_user.id)
        .order_by(BoostOrder.created_at.desc())
        .limit(50)
        .all()
    )

    # Résolution event_title sans N+1
    event_ids = list({b.event_id for b in boosts})
    events_map = {
        e.id: e.title
        for e in db.query(Event.id, Event.title).filter(Event.id.in_(event_ids)).all()
    } if event_ids else {}

    return [
        BoostOrderOut(
            id=b.id,
            event_id=b.event_id,
            event_title=events_map.get(b.event_id, "Événement supprimé"),
            package_code=b.package_code,
            duration_days=b.duration_days,
            amount_fcfa=b.amount_fcfa,
            status=b.status,
            starts_at=b.starts_at,
            ends_at=b.ends_at,
            created_at=b.created_at,
        )
        for b in boosts
    ]


@router.post("/organizer/fb-package/request", response_model=FbPackRequestOut)
async def request_fb_package(
    data: FbPackRequestIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    L'organisateur demande un Pack Facebook Ads.
    total_paid = budget_fb × (1 + 30%). Le CM traitera après paiement.
    """
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès réservé aux organisateurs")

    event = _check_event_ownership(db, data.event_id, current_user)

    # Garde minimum (déjà validé Pydantic mais double-sécurité)
    if data.budget_fb_fcfa < FB_PACK_MIN_BUDGET:
        raise HTTPException(400, f"Budget Facebook minimum : {FB_PACK_MIN_BUDGET} FCFA")

    # Calcul marge + total
    margin = int(round(data.budget_fb_fcfa * FB_PACK_MARGIN_RATE))
    total = data.budget_fb_fcfa + margin

    # Order ID + UTM unique
    order_id = f"FBP-{uuid.uuid4().hex[:16].upper()}"
    utm = f"fb-{_slugify_event_for_utm(event.title)}-{event.id}-{uuid.uuid4().hex[:6]}"

    pack = FbAdPackage(
        organizer_id=current_user.id,
        event_id=event.id,
        budget_fb_fcfa=data.budget_fb_fcfa,
        margin_fcfa=margin,
        total_paid_fcfa=total,
        age_range=data.age_range,
        zone=data.zone,
        notes=data.notes,
        utm_campaign=utm,
        status="en_attente_om",
        payment_ref=order_id,
    )
    db.add(pack)
    db.flush()

    # Init paiement NGSER
    try:
        status_code, response_data = await create_payment_session(
            order_id=order_id,
            amount=total,
        )
    except Exception as e:
        db.delete(pack)
        db.commit()
        print(f"❌ Erreur réseau NGSER (Pack FB) : {str(e)}")
        raise HTTPException(503, "Le service de paiement est temporairement indisponible.")

    if status_code != 200 or "payment_url" not in response_data:
        db.delete(pack)
        db.commit()
        print(f"❌ NGSER init Pack FB : HTTP {status_code} | {response_data}")
        raise HTTPException(400, "Impossible d'initialiser le paiement Orange Money.")

    payment_url = response_data["payment_url"]

    db.commit()

    log_security_event(
        db, "fbpack_requested", None, current_user.id,
        f"FB Pack event {event.id} - budget {data.budget_fb_fcfa} - total {total} FCFA",
    )

    return FbPackRequestOut(
        payment_url=payment_url,
        payment_ref=order_id,
        budget_fb_fcfa=data.budget_fb_fcfa,
        margin_fcfa=margin,
        total_paid_fcfa=total,
        fb_package_id=pack.id,
    )


@router.get("/organizer/fb-package/my", response_model=List[FbPackOut])
def list_my_fb_packages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Liste des Pack FB de l'organisateur."""
    if current_user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès réservé aux organisateurs")

    packs = (
        db.query(FbAdPackage)
        .filter(FbAdPackage.organizer_id == current_user.id)
        .order_by(FbAdPackage.created_at.desc())
        .limit(50)
        .all()
    )

    event_ids = list({p.event_id for p in packs})
    events_map = {
        e.id: e.title
        for e in db.query(Event.id, Event.title).filter(Event.id.in_(event_ids)).all()
    } if event_ids else {}

    return [
        FbPackOut(
            id=p.id,
            event_id=p.event_id,
            event_title=events_map.get(p.event_id, "Événement supprimé"),
            budget_fb_fcfa=p.budget_fb_fcfa,
            margin_fcfa=p.margin_fcfa,
            total_paid_fcfa=p.total_paid_fcfa,
            age_range=p.age_range,
            zone=p.zone,
            notes=p.notes,
            utm_campaign=p.utm_campaign,
            status=p.status,
            fb_campaign_id=p.fb_campaign_id,
            cm_notes=p.cm_notes,
            created_at=p.created_at,
            launched_at=p.launched_at,
        )
        for p in packs
    ]


# ═════════════════════════════════════════════════════════════════════
# ROUTES ADMIN / CM
# ═════════════════════════════════════════════════════════════════════

@router.get(
    "/admin/fb-packages",
    response_model=List[FbPackOut],
    dependencies=[Depends(require_staff_function("cm"))],
)
def admin_list_fb_packages(
    status: str = None,
    db: Session = Depends(get_db),
):
    """
    Le CM voit toutes les demandes Pack FB.
    Filtrable par statut (typiquement 'paye_attente_cm' pour sa queue de travail).
    """
    q = db.query(FbAdPackage)
    if status:
        q = q.filter(FbAdPackage.status == status)
    packs = q.order_by(FbAdPackage.created_at.desc()).limit(100).all()

    event_ids = list({p.event_id for p in packs})
    events_map = {
        e.id: e.title
        for e in db.query(Event.id, Event.title).filter(Event.id.in_(event_ids)).all()
    } if event_ids else {}

    return [
        FbPackOut(
            id=p.id,
            event_id=p.event_id,
            event_title=events_map.get(p.event_id, "Événement supprimé"),
            budget_fb_fcfa=p.budget_fb_fcfa,
            margin_fcfa=p.margin_fcfa,
            total_paid_fcfa=p.total_paid_fcfa,
            age_range=p.age_range,
            zone=p.zone,
            notes=p.notes,
            utm_campaign=p.utm_campaign,
            status=p.status,
            fb_campaign_id=p.fb_campaign_id,
            cm_notes=p.cm_notes,
            created_at=p.created_at,
            launched_at=p.launched_at,
        )
        for p in packs
    ]


@router.patch(
    "/admin/fb-packages/{pack_id}",
    response_model=FbPackOut,
    dependencies=[Depends(require_staff_function("cm"))],
)
def admin_update_fb_package(
    pack_id: int,
    data: FbPackAdminUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    CM met à jour un Pack FB :
      - action='launch'   : statut paye_attente_cm → actif, fb_campaign_id requis
      - action='complete' : statut actif → termine
      - action='cancel'   : annulation (statut → annule)
    """
    pack = db.query(FbAdPackage).filter(FbAdPackage.id == pack_id).with_for_update().first()
    if not pack:
        raise HTTPException(404, "Pack FB introuvable")

    now = datetime.utcnow()

    if data.action == "launch":
        if pack.status != "paye_attente_cm":
            raise HTTPException(409, f"Statut actuel '{pack.status}' incompatible avec 'launch'")
        if not data.fb_campaign_id:
            raise HTTPException(400, "fb_campaign_id requis pour lancer")
        pack.status = "actif"
        pack.fb_campaign_id = data.fb_campaign_id
        pack.launched_at = now
        if data.cm_notes:
            pack.cm_notes = data.cm_notes

    elif data.action == "complete":
        if pack.status != "actif":
            raise HTTPException(409, f"Statut actuel '{pack.status}' incompatible avec 'complete'")
        pack.status = "termine"
        pack.ended_at = now
        if data.cm_notes:
            pack.cm_notes = data.cm_notes

    elif data.action == "cancel":
        if pack.status in ("termine", "annule"):
            raise HTTPException(409, f"Pack déjà en statut '{pack.status}'")
        pack.status = "annule"
        pack.ended_at = now
        if data.cm_notes:
            pack.cm_notes = data.cm_notes

    db.commit()
    db.refresh(pack)

    log_security_event(
        db, f"fbpack_{data.action}", None, current_user.id,
        f"Pack FB #{pack.id} action {data.action}",
    )

    # Event title pour la réponse
    event = db.query(Event.title).filter(Event.id == pack.event_id).first()
    event_title = event.title if event else "Événement supprimé"

    return FbPackOut(
        id=pack.id,
        event_id=pack.event_id,
        event_title=event_title,
        budget_fb_fcfa=pack.budget_fb_fcfa,
        margin_fcfa=pack.margin_fcfa,
        total_paid_fcfa=pack.total_paid_fcfa,
        age_range=pack.age_range,
        zone=pack.zone,
        notes=pack.notes,
        utm_campaign=pack.utm_campaign,
        status=pack.status,
        fb_campaign_id=pack.fb_campaign_id,
        cm_notes=pack.cm_notes,
        created_at=pack.created_at,
        launched_at=pack.launched_at,
    )

# ═════════════════════════════════════════════════════════════════════
# SERVICES PREMIUM (pré-inscription publique)
# ═════════════════════════════════════════════════════════════════════
from typing import Optional
from pydantic import EmailStr
from models.premium_interest import PremiumInterest


class PremiumInterestIn(BaseModel):
    email: EmailStr
    services: Optional[str] = Field(None, max_length=500)


@router.post("/premium-interest")
def register_premium_interest(
    data: PremiumInterestIn,
    db: Session = Depends(get_db),
):
    """
    Public : capture d'email pour les Services Premium à venir.
    Pas d'auth requise (formulaire marketing sur partenaire.html).
    Anti-spam : un email ne peut s'inscrire qu'une fois (idempotent).
    """
    email_lower = data.email.lower().strip()

    # Idempotence : si déjà inscrit, on ne crée pas de doublon
    existing = (
        db.query(PremiumInterest)
        .filter(func.lower(PremiumInterest.email) == email_lower)
        .first()
    )
    if existing:
        return {"status": "already_registered", "message": "Vous êtes déjà sur la liste d'attente"}

    interest = PremiumInterest(
        email=email_lower,
        services=data.services or None,
        source="partenaire_page",
    )
    db.add(interest)
    db.commit()

    return {"status": "success", "message": "Inscription enregistrée. Vous serez notifié au lancement."}
