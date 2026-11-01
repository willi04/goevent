"""
api/staff.py — Gestion des employés staff GoEvent.
Routes admin pour créer/modifier/lister les commerciaux, CMs, designers.
Plus route /admin/staff/me/permissions pour que le front sache quoi afficher.
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core import sales
from core.database import get_db
from core.limiter import limiter
from core.sales_docs import welcome_text
from core.security import hash_pin, verify_pin, log_security_event, get_current_user
from core.permissions import (
    get_current_admin,
    normalize_role,
    is_admin,
    is_admin_or_staff,
    STAFF_FUNCTIONS_VALIDES,
)
from models.user import User
from schemas.staff import (
    StaffCreate, StaffCreatedOut, StaffUpdate, StaffOut, PermissionsOut, TransferIn, WelcomePdfIn,
)

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
        "fb_packages_manage": False,
        
        # Gestion équipe + commissions
        "staff_manage": False,
        "commissions_view": False,
        "commissions_approve": False,
        
        # Modération
        "moderate_partners": False,
        "moderate_cancellations": False,
        "moderate_feedbacks": False,

        # Back-office contenu du site (textes, images, FAQ) / pages légales
        "content_manage": False,
        "legal_pages_manage": False,
        "pricing_manage": False,

        # Invitations digitales (événements privés) : gestion / activation des packs
        "invitations_manage": False,
        "invitations_activate": False,
        "invitations_catalog": False,      # modifier le catalogue des packs (admin seul)
    }
    
    if role == "admin":
        return {k: True for k in caps}
    
    if role == "staff":
        if fn == "commercial":
            caps.update({
                # Pas de view_users / view_finance (lot 4.5) : un commercial ne voit ni la
                # liste des comptes ni la finance ; ses ventes sont dans Invitations.
                "view_dashboard": True,
                "view_events": True,
                "ads_contracts_read": True,
                "ads_contracts_write": True,
                "ads_slides_read": True,
                "moderate_partners": True,
                "commissions_view": True,
                "invitations_manage": True,
            })
        elif fn == "cm":
            caps.update({
                "view_dashboard": True,
                "view_events": True,
                "boost_visibility_manage": True,
                "fb_ads_manage": True,
                "fb_packages_manage": True,
                "moderate_feedbacks": True,
                "commissions_view": True,
                "content_manage": True,
                 # ─── Capabilities ex-designer absorbées temporairement ───
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


def _created_out(staff: User, pin: str) -> dict:
    out = StaffOut.model_validate(staff).model_dump()
    out.update({"temporary_pin": pin, "welcome_text": welcome_text(staff, pin)})
    return out


@router.post("/admin/staff", response_model=StaffCreatedOut)
@limiter.limit("20/minute")
def create_staff(
    payload: StaffCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Crée un nouvel employé staff (rôle='staff' + sous-fonction).
    PIN provisoire généré par le serveur (secrets), renvoyé une seule fois ;
    changement obligatoire à la première connexion (must_change_pin).
    Commercial : code commercial proposé automatiquement (COM-PRENOM) si vide.
    """
    existing = db.query(User).filter(User.phone_number == payload.phone_number).first()
    if existing:
        raise HTTPException(409, "Ce numéro est déjà associé à un compte")
    code = sales.clean_sales_code(db, payload.sales_code)
    if not code and payload.staff_function in sales.SALES_FUNCTIONS:
        code = sales.suggest_sales_code(db, payload.full_name)
    pin = sales.generate_pin()
    new_staff = User(
        full_name=payload.full_name.strip(),
        phone_number=payload.phone_number,
        email=payload.email,
        pin_hash=hash_pin(pin),
        role="staff",
        staff_function=payload.staff_function,
        commission_rate=payload.commission_rate,
        sales_code=code,
        must_change_pin=True,
        is_active=True,
    )
    db.add(new_staff)
    db.commit()
    db.refresh(new_staff)
    log_security_event(
        db, "staff_created", request, admin.id,
        f"Staff #{new_staff.id} créé ({payload.staff_function}, {new_staff.full_name}, code {code or '—'})"
    )
    return _created_out(new_staff, pin)


@router.get("/admin/staff/suggest-code")
def suggest_code(name: str, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    return {"sales_code": sales.suggest_sales_code(db, name[:100])}


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
    if "sales_code" in data:
        new_code = sales.clean_sales_code(db, data["sales_code"], exclude_id=staff.id)
        if (new_code or None) != (staff.sales_code or None) and staff.sales_code and sales.sales_code_locked(db, staff.id):
            raise HTTPException(409, "Code commercial définitif : il est déjà rattaché à des devis, événements ou ventes.")
        data["sales_code"] = new_code
    if data.get("staff_function") in sales.SALES_FUNCTIONS and not staff.sales_code and not data.get("sales_code"):
        data["sales_code"] = sales.suggest_sales_code(db, data.get("full_name") or staff.full_name, exclude_id=staff.id)
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
    Génère un nouveau PIN aléatoire à 6 chiffres pour un staff
    (utile si l'employé a oublié son PIN).
    Le PIN est retourné UNE seule fois — l'admin doit le transmettre
    à l'employé qui devra le changer immédiatement à la connexion.
    """
    staff = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff:
        raise HTTPException(404, "Employé staff introuvable")
    
    # PIN provisoire : 6 chiffres via secrets (jamais random), à changer à la connexion
    new_pin = sales.generate_pin()
    staff.pin_hash = hash_pin(new_pin)
    staff.must_change_pin = True
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
        "staff_id": staff.id,
        "welcome_text": welcome_text(staff, new_pin),
    }


@router.post("/admin/staff/{staff_id}/welcome.pdf")
@limiter.limit("20/minute")
def staff_welcome_pdf(
    staff_id: int,
    data: WelcomePdfIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Fiche de bienvenue PDF. Le PIN provisoire est revérifié (hash + must_change_pin) puis
    imprimé dans le PDF ; il n'est ni stocké ni journalisé. Après changement du PIN par
    l'employé, la fiche ne peut plus être produite (réinitialiser le PIN)."""
    staff = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff:
        raise HTTPException(404, "Employé staff introuvable")
    if not staff.must_change_pin or not verify_pin(data.pin, staff.pin_hash):
        raise HTTPException(400, "PIN provisoire invalide ou déjà remplacé : réinitialisez le PIN pour produire une fiche.")
    from core.sales_docs import welcome_pdf
    pdf = welcome_pdf(staff, data.pin)
    log_security_event(db, "staff_welcome_pdf", request, admin.id, f"Fiche de bienvenue staff #{staff.id}")
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="goevent-bienvenue-%s.pdf"' % (staff.sales_code or staff.id),
        "Cache-Control": "no-store"})


@router.post("/admin/staff/{staff_id}/transfer")
@limiter.limit("10/minute")
def staff_transfer(
    staff_id: int,
    data: TransferIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Transfère le portefeuille (devis non convertis + événements privés) vers un autre commercial
    ou l'administration. Les ventes et commissions déjà acquises restent au vendeur d'origine."""
    from models.invitation import PrivateEventSettings
    from models.invitation_quote import InvitationQuote
    src = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not src:
        raise HTTPException(404, "Employé staff introuvable")
    target_id = data.to_user_id
    if target_id:
        dst = db.query(User).filter(User.id == target_id, User.is_active == True).first()  # noqa: E712
        if not dst or dst.id == src.id or not (sales.is_seller(dst) or is_admin(dst)):
            raise HTTPException(400, "Destinataire invalide (commercial actif ou administrateur).")
    else:
        target_id = admin.id
    n_q = (db.query(InvitationQuote).filter(InvitationQuote.owner_id == src.id, InvitationQuote.status != "converti")
           .update({InvitationQuote.owner_id: target_id}, synchronize_session=False))
    ev_owner = None if not data.to_user_id or is_admin(db.get(User, target_id)) else target_id
    n_e = (db.query(PrivateEventSettings).filter(PrivateEventSettings.owner_id == src.id)
           .update({PrivateEventSettings.owner_id: ev_owner}, synchronize_session=False))
    db.commit()
    log_security_event(db, "sales_portfolio_transferred", request, admin.id,
                       f"Portefeuille staff #{src.id} → #{target_id} : {n_q} devis, {n_e} événement(s)")
    return {"quotes": n_q, "events": n_e}
