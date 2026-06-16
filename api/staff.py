"""
api/staff.py — Gestion des employés staff GoEvent.
Routes admin pour créer/modifier/lister les commerciaux, CMs, designers.
Plus route /admin/staff/me/permissions pour que le front sache quoi afficher.
"""
import random
import string
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import hash_pin, log_security_event, get_current_user
from core.permissions import (
    get_current_admin,
    normalize_role,
    is_admin,
    is_admin_or_staff,
    STAFF_FUNCTIONS_VALIDES,
)
from models.user import User
from schemas.staff import StaffCreate, StaffUpdate, StaffOut, PermissionsOut

router = APIRouter(tags=["staff"])


# ════════════════════════════════════════════════════════════
# CAPABILITIES — qui peut faire quoi
# ════════════════════════════════════════════════════════════

def _capabilities_for(user: User) -> dict:
    """
    Retourne un dict des capacités du user (utilisé par le frontend
    pour afficher/cacher les onglets et boutons).
    
    Convention :
    - L'admin a tout par défaut.
    - Chaque staff_function débloque un sous-ensemble.
    - Un fan/organizer/agent n'a aucune capability admin.
    """
    role = normalize_role(user.role)
    fn = user.staff_function
    
    caps = {
        # Lecture seule générale (dashboard, stats)
        "view_dashboard": False,
        "view_users": False,
        "view_events": False,
        "view_finance": False,
        
        # Régie publicitaire externe (annonceurs)
        "ads_contracts_read": False,
        "ads_contracts_write": False,
        
        # Affiches / slides
        "ads_slides_read": False,
        "ads_slides_write": False,
        
        # Boost visibilité organisateurs + Pack FB Ads (Chantier 9.x)
        "boost_visibility_manage": False,
        "fb_ads_manage": False,
        
        # Gestion équipe + commissions
        "staff_manage": False,
        "commissions_view": False,
        "commissions_approve": False,
        
        # Modération
        "moderate_partners": False,
        "moderate_cancellations": False,
        "moderate_feedbacks": False,
    }
    
    if role == "admin":
        return {k: True for k in caps}
    
    if role == "staff":
        if fn == "commercial":
            caps.update({
                "view_dashboard": True,
                "view_users": True,
                "view_events": True,
                "view_finance": True,
                "ads_contracts_read": True,
                "ads_contracts_write": True,
                "ads_slides_read": True,
                "moderate_partners": True,
                "commissions_view": True,
            })
        elif fn == "cm":
            caps.update({
                "view_dashboard": True,
                "view_events": True,
                "boost_visibility_manage": True,
                "fb_ads_manage": True,
                "moderate_feedbacks": True,
                "commissions_view": True,
                 # ─── Capabilities ex-designer absorbées temporairement ───
                # À RETIRER le jour où un Designer dédié est embauché.
                # Cf. TECH_Core dette P3 — "Capabilities designer chez CM".
                "ads_slides_read": True,
                "ads_slides_write": True,
            })
        elif fn == "designer":
            caps.update({
                "view_dashboard": True,
                "view_events": True,
                "ads_slides_read": True,
                "ads_slides_write": True,
            })
    
    return caps


# ════════════════════════════════════════════════════════════
# PERMISSIONS DU USER COURANT (pour le frontend)
# ════════════════════════════════════════════════════════════

@router.get("/admin/staff/me/permissions", response_model=PermissionsOut)
def get_my_permissions(user: User = Depends(get_current_user)):
    """
    Renvoie les permissions du user connecté. Utilisé par admin.html
    au chargement pour afficher/masquer les onglets selon le profil.
    
    Accessible à tout user authentifié (un fan recevra is_admin=false
    et un dict capabilities vide, c'est le comportement attendu).
    """
    return PermissionsOut(
        user_id=user.id,
        role=user.role,
        role_normalized=normalize_role(user.role),
        staff_function=user.staff_function,
        is_admin=is_admin(user),
        is_staff=is_admin_or_staff(user),
        capabilities=_capabilities_for(user),
    )


# ════════════════════════════════════════════════════════════
# CRUD STAFF — admin uniquement
# ════════════════════════════════════════════════════════════

@router.get("/admin/staff", response_model=List[StaffOut])
def list_staff(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Liste tous les employés staff (actifs et désactivés)."""
    staff_list = (
        db.query(User)
        .filter(User.role == "staff")
        .order_by(User.created_at.desc())
        .all()
    )
    return staff_list


@router.post("/admin/staff", response_model=StaffOut)
def create_staff(
    payload: StaffCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Crée un nouvel employé staff (rôle='staff' + sous-fonction).
    PIN initial fourni par l'admin, l'employé doit le changer à la 1re connexion.
    """
    # Unicité du numéro
    existing = db.query(User).filter(User.phone_number == payload.phone_number).first()
    if existing:
        raise HTTPException(409, "Ce numéro est déjà associé à un compte")
    
    # Création
    new_staff = User(
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        email=payload.email,
        pin_hash=hash_pin(payload.pin_code),
        role="staff",
        staff_function=payload.staff_function,
        commission_rate=payload.commission_rate,
        is_active=True,
    )
    db.add(new_staff)
    db.commit()
    db.refresh(new_staff)
    
    # Audit
    log_security_event(
        db, "staff_created", request, admin.id,
        f"Staff #{new_staff.id} créé ({payload.staff_function}, {payload.full_name})"
    )
    
    return new_staff


@router.patch("/admin/staff/{staff_id}", response_model=StaffOut)
def update_staff(
    staff_id: int,
    payload: StaffUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Met à jour un staff. Le PIN ne se modifie PAS ici (route dédiée /reset-pin)."""
    staff = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff:
        raise HTTPException(404, "Employé staff introuvable")
    
    data = payload.model_dump(exclude_unset=True)
    changes = []
    for k, v in data.items():
        old = getattr(staff, k, None)
        if old != v:
            changes.append(f"{k}: {old}→{v}")
            setattr(staff, k, v)
    
    db.commit()
    db.refresh(staff)
    
    if changes:
        log_security_event(
            db, "staff_updated", request, admin.id,
            f"Staff #{staff_id} modifié : {'; '.join(changes)}"
        )
    
    return staff


@router.delete("/admin/staff/{staff_id}")
def delete_staff(
    staff_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Soft-delete : désactive le compte (is_active=false).
    On NE supprime PAS pour conserver l'historique des commissions versées.
    """
    if staff_id == admin.id:
        raise HTTPException(400, "Vous ne pouvez pas vous désactiver vous-même")
    
    staff = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff:
        raise HTTPException(404, "Employé staff introuvable")
    
    if not staff.is_active:
        return {"message": "Ce compte est déjà désactivé"}
    
    staff.is_active = False
    db.commit()
    
    log_security_event(
        db, "staff_deactivated", request, admin.id,
        f"Staff #{staff_id} ({staff.full_name}) désactivé"
    )
    
    return {"message": f"Compte de {staff.full_name} désactivé"}


@router.post("/admin/staff/{staff_id}/reset-pin")
def reset_staff_pin(
    staff_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Génère un nouveau PIN aléatoire à 4 chiffres pour un staff
    (utile si l'employé a oublié son PIN).
    Le PIN est retourné UNE seule fois — l'admin doit le transmettre
    à l'employé qui devra le changer immédiatement à la connexion.
    """
    staff = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff:
        raise HTTPException(404, "Employé staff introuvable")
    
    # Génération PIN 4 chiffres aléatoires
    new_pin = "".join(random.choices(string.digits, k=4))
    staff.pin_hash = hash_pin(new_pin)
    staff.failed_login_attempts = 0
    staff.locked_until = None
    db.commit()
    
    log_security_event(
        db, "staff_pin_reset", request, admin.id,
        f"PIN reset pour staff #{staff_id} ({staff.full_name})"
    )
    
    return {
        "message": "PIN réinitialisé. Transmettez ce code à l'employé qui devra le changer immédiatement.",
        "temporary_pin": new_pin,
        "staff_name": staff.full_name,
        "staff_phone": staff.phone_number,
    }
