"""api/ads.py — Routes régie publicitaire (admin + public)."""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from core.permissions import require_staff_function
from core.limiter import limiter
from models.user import User
from models.cms import HomepageContent, AdContract
from schemas.ads import (
    AdContractCreate, AdContractUpdate, AdContractOut,
    HomepageSlideCreate, HomepageSlideUpdate,
    HomepageSlideOut, HomepageSlidePublic,
)

router = APIRouter(tags=["ads"])

# ════════════════════════════════════════════════════════════
# Constantes métier
# ════════════════════════════════════════════════════════════

MAX_AD_SLIDES_OVERLAP = 4         # max commerciales simultanées
MAX_SYSTEM_SLIDES_OVERLAP = 1     # max système simultanées
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 Mo brut (avant compression front)

def _check_image_size(image_url: Optional[str]):
    """Refuse les base64 > 10 Mo. URLs externes (http://) ignorées."""
    if not image_url:
        return
    if image_url.startswith("data:"):
        # base64 = ~4/3 du poids binaire. On approxime la taille brute.
        b64_payload = image_url.split(",", 1)[-1]
        approx_bytes = (len(b64_payload) * 3) // 4
        if approx_bytes > MAX_IMAGE_BYTES:
            raise HTTPException(413, f"Image trop lourde (max {MAX_IMAGE_BYTES // (1024*1024)} Mo). Réduisez la taille avant upload.")


def _count_overlap(db: Session, slide_type: str, starts_at: datetime, ends_at: Optional[datetime], exclude_id: Optional[int] = None) -> int:
    """
    Compte le pic de slides actives chevauchant la période [starts_at, ends_at]
    pour un type donné ('ad' ou 'system').
    
    Algorithme : on prend toutes les slides du même type qui ont au moins
    UN instant en commun avec la nouvelle période. C'est le worst-case du
    chevauchement — si ce nombre dépasse le quota, on refuse.
    
    NB : ends_at NULL = slide permanente, considérée comme chevauchant tout futur.
    """
    new_end = ends_at if ends_at else datetime(2099, 12, 31)
    
    q = db.query(HomepageContent).filter(
        HomepageContent.is_active == True,
        HomepageContent.type == slide_type,
        # Chevauchement : (A.start <= B.end) AND (A.end >= B.start)
        or_(
            HomepageContent.starts_at == None,
            HomepageContent.starts_at <= new_end,
        ),
        or_(
            HomepageContent.ends_at == None,
            HomepageContent.ends_at >= starts_at,
        ),
    )
    if exclude_id:
        q = q.filter(HomepageContent.id != exclude_id)
    return q.count()


def _slide_to_out(slide: HomepageContent, db: Session) -> dict:
    """Sérialise une slide pour la vue admin avec jointure sponsor."""
    data = {
        "id": slide.id,
        "type": slide.type,
        "title": slide.title,
        "subtitle": slide.subtitle,
        "image_url": slide.image_url,
        "cta_text": slide.cta_text,
        "cta_link": slide.cta_link,
        "position": slide.position,
        "is_active": slide.is_active,
        "starts_at": slide.starts_at,
        "ends_at": slide.ends_at,
        "ad_contract_id": slide.ad_contract_id,
        "created_at": slide.created_at,
        "sponsor_name": None,
    }
    if slide.ad_contract_id:
        contract = db.query(AdContract).filter(AdContract.id == slide.ad_contract_id).first()
        if contract:
            data["sponsor_name"] = contract.sponsor_name
    return data


# ════════════════════════════════════════════════════════════
# ROUTES ADMIN — CONTRATS
# ════════════════════════════════════════════════════════════
@router.post("/admin/ads/contracts", response_model=AdContractOut)
def create_contract(
    payload: AdContractCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_staff_function("commercial")),
):
    contract = AdContract(**payload.model_dump(), created_by=user.id)
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return contract


@router.get("/admin/ads/contracts", response_model=List[AdContractOut])
def list_contracts(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(require_staff_function("commercial")),
):
    q = db.query(AdContract).order_by(AdContract.created_at.desc())
    if status_filter:
        q = q.filter(AdContract.status == status_filter)
    return q.all()


@router.patch("/admin/ads/contracts/{contract_id}", response_model=AdContractOut)
def update_contract(
    contract_id: int,
    payload: AdContractUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_staff_function("commercial")),
):
    contract = db.query(AdContract).filter(AdContract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrat introuvable")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(contract, k, v)
    db.commit()
    db.refresh(contract)
    return contract


@router.delete("/admin/ads/contracts/{contract_id}")
def delete_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_staff_function("commercial")),
):
    contract = db.query(AdContract).filter(AdContract.id == contract_id).first()
    if not contract:
        raise HTTPException(404, "Contrat introuvable")
    linked = db.query(HomepageContent).filter(HomepageContent.ad_contract_id == contract_id).count()
    if linked > 0:
        raise HTTPException(409, f"Contrat utilisé par {linked} slide(s). Supprimez d'abord les slides liées.")
    db.delete(contract)
    db.commit()
    return {"ok": True}


# ════════════════════════════════════════════════════════════
# ROUTES ADMIN — SLIDES
# ════════════════════════════════════════════════════════════

@router.post("/admin/ads/slides", response_model=dict)
def create_slide(
    payload: HomepageSlideCreate,
    slide_type: str = Query("ad", pattern="^(ad|system)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_staff_function("cm", "designer")),
):
    """
    Crée une slide. `slide_type` distingue commercial ('ad') vs maison ('system').
    Vérification du quota par chevauchement temporel.
    Accès : admin + cm + designer (création visuelle = équipe créative).
    """
    _check_image_size(payload.image_url)

    if slide_type == "ad" and not payload.ad_contract_id:
        raise HTTPException(422, "Une slide commerciale doit être liée à un contrat (ad_contract_id requis)")

    if payload.ad_contract_id:
        contract = db.query(AdContract).filter(AdContract.id == payload.ad_contract_id).first()
        if not contract:
            raise HTTPException(404, "Contrat introuvable")

    if payload.is_active:
        max_overlap = MAX_AD_SLIDES_OVERLAP if slide_type == "ad" else MAX_SYSTEM_SLIDES_OVERLAP
        starts = payload.starts_at or datetime.utcnow()
        overlap = _count_overlap(db, slide_type, starts, payload.ends_at)
        if overlap >= max_overlap:
            label = "commerciales" if slide_type == "ad" else "système"
            raise HTTPException(
                409,
                f"Limite atteinte : {max_overlap} slide(s) {label} déjà programmée(s) sur cette période. Désactivez ou décalez une slide existante."
            )

    slide = HomepageContent(
        type=slide_type,
        title=payload.title,
        subtitle=payload.subtitle,
        image_url=payload.image_url,
        cta_text=payload.cta_text,
        cta_link=payload.cta_link,
        position=payload.position,
        is_active=payload.is_active,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        ad_contract_id=payload.ad_contract_id,
        created_by=user.id,
    )
    db.add(slide)
    db.commit()
    db.refresh(slide)
    return _slide_to_out(slide, db)

@router.get("/admin/ads/slides", response_model=List[dict])
def list_slides(
    type_filter: Optional[str] = Query(None, alias="type", pattern="^(ad|system)$"),
    active_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_staff_function("commercial", "cm", "designer")),
):
    """Lecture ouverte à tout staff : commercial doit voir ce que ses annonceurs ont en diffusion."""
    q = db.query(HomepageContent)
    if type_filter:
        q = q.filter(HomepageContent.type == type_filter)
    if active_only:
        q = q.filter(HomepageContent.is_active == True)
    slides = q.order_by(HomepageContent.position.asc(), HomepageContent.created_at.desc()).all()
    return [_slide_to_out(s, db) for s in slides]


@router.patch("/admin/ads/slides/{slide_id}", response_model=dict)
def update_slide(
    slide_id: int,
    payload: HomepageSlideUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_staff_function("cm", "designer")),
):
    slide = db.query(HomepageContent).filter(HomepageContent.id == slide_id).first()
    if not slide:
        raise HTTPException(404, "Slide introuvable")

    data = payload.model_dump(exclude_unset=True)
    _check_image_size(data.get("image_url"))

    new_active = data.get("is_active", slide.is_active)
    new_starts = data.get("starts_at", slide.starts_at)
    new_ends = data.get("ends_at", slide.ends_at)
    will_overlap_change = "is_active" in data or "starts_at" in data or "ends_at" in data

    if new_active and will_overlap_change:
        max_overlap = MAX_AD_SLIDES_OVERLAP if slide.type == "ad" else MAX_SYSTEM_SLIDES_OVERLAP
        starts = new_starts or datetime.utcnow()
        overlap = _count_overlap(db, slide.type, starts, new_ends, exclude_id=slide.id)
        if overlap >= max_overlap:
            label = "commerciales" if slide.type == "ad" else "système"
            raise HTTPException(
                409,
                f"Modification refusée : {max_overlap} slide(s) {label} déjà programmée(s) sur cette période."
            )

    for k, v in data.items():
        setattr(slide, k, v)
    db.commit()
    db.refresh(slide)
    return _slide_to_out(slide, db)


@router.delete("/admin/ads/slides/{slide_id}")
def delete_slide(
    slide_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_staff_function("cm", "designer")),
):
    slide = db.query(HomepageContent).filter(HomepageContent.id == slide_id).first()
    if not slide:
        raise HTTPException(404, "Slide introuvable")
    db.delete(slide)
    db.commit()
    return {"ok": True}

# ════════════════════════════════════════════════════════════
# ROUTE PUBLIQUE — Carrousel accueil
# ════════════════════════════════════════════════════════════

@router.get("/ads/homepage", response_model=List[HomepageSlidePublic])
def get_homepage_slides(request: Request, db: Session = Depends(get_db)):
    """
    Renvoie les slides à afficher MAINTENANT (now ∈ [starts_at, ends_at])
    triées par position. Vue expurgée : zéro donnée commerciale.
    Cache navigateur 60s.
    """
    now = datetime.utcnow()
    slides = db.query(HomepageContent).filter(
        HomepageContent.is_active == True,
        or_(HomepageContent.starts_at == None, HomepageContent.starts_at <= now),
        or_(HomepageContent.ends_at == None, HomepageContent.ends_at >= now),
    ).order_by(HomepageContent.position.asc(), HomepageContent.id.asc()).all()
    
    # On respecte les quotas en sortie aussi (filet de sécurité)
    ad_slides = [s for s in slides if s.type == "ad"][:MAX_AD_SLIDES_OVERLAP]
    sys_slides = [s for s in slides if s.type == "system"][:MAX_SYSTEM_SLIDES_OVERLAP]
    final = sorted(ad_slides + sys_slides, key=lambda s: (s.position, s.id))
    
    return final
