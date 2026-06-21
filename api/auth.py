"""
api/auth.py
Routeur authentification : register, login, refresh, profil, PIN,
2FA (TOTP), mot de passe oublié / reset.
"""
import random
import string
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_db
from core.limiter import limiter
from core.security import (
    hash_pin, verify_pin,
    create_access_token, create_refresh_token,
    get_current_user, log_security_event,
)
from core.email import envoyer_mail_code
from models.user import User, PasswordReset
from schemas.auth import (
    UserRegister, UserLogin, RefreshTokenRequest,
    UserUpdate, PinChange,
)

router = APIRouter()

from core.permissions import ROLES_VALIDES


# ── REGISTER ────────────────────────────────────────────────────
@router.post("/auth/register")
@limiter.limit("3/minute")
def register(request: Request, data: UserRegister, db: Session = Depends(get_db)):
    # Inscription publique : seulement fan/organizer/agent autorisés.
    # admin/staff sont créés via routes admin dédiées (Commit 8.4).
    # partner est réservé pour usage futur, non inscriptible publiquement.
    ROLES_INSCRIPTIBLES = ("fan", "organizer", "agent")
    if data.role not in ROLES_INSCRIPTIBLES:
        raise HTTPException(400, f"Rôle invalide pour inscription publique. Valeurs : {ROLES_INSCRIPTIBLES}")

    if len(data.pin_code) != 4 or not data.pin_code.isdigit():
        raise HTTPException(400, "Le PIN doit être 4 chiffres")
    if db.query(User).filter(User.phone_number == data.phone_number).first():
        raise HTTPException(400, "Ce numéro est déjà utilisé")

    user = User(
        phone_number=data.phone_number,
        full_name=data.full_name,
        email=data.email,
        pin_hash=hash_pin(data.pin_code),
        role=data.role,
        org_name=data.org_name,
        org_type=data.org_type,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "access_token": create_access_token(user.id, user.role),
        "refresh_token": create_refresh_token(user.id, user.role),
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "id": user.id,
            "name": user.full_name,
            "role": user.role,
            "phone_number": user.phone_number,
            "phone": user.phone_number,
            "email": user.email,
            "org_name": user.org_name,
            "org_type": user.org_type,
            "staff_function": user.staff_function,
        },
    }


# ── LOGIN ───────────────────────────────────────────────────────
@router.post("/auth/login")
@limiter.limit("5/minute")
def login(request: Request, data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone_number == data.phone_number).first()

    if not user:
        log_security_event(db, "login_failed", request,
                           details=f"Numéro inconnu : {data.phone_number}")
        raise HTTPException(401, "Numéro ou PIN incorrect")

    if not user.is_active:
        log_security_event(db, "login_blocked_inactive", request, user.id,
                           f"Tentative sur compte désactivé (rôle: {user.role})")
        raise HTTPException(403, "Ce compte a été désactivé. Contactez votre administrateur si vous pensez qu'il s'agit d'une erreur.")

    if user.locked_until and user.locked_until > datetime.utcnow():
        log_security_event(db, "login_blocked", request, user.id,
                           "Tentative sur compte verrouillé")
        minutes_restantes = int((user.locked_until - datetime.utcnow()).total_seconds() / 60) + 1
        raise HTTPException(403, f"Compte temporairement verrouillé. Réessayez dans {minutes_restantes} minute(s).")

    if not verify_pin(data.pin_code, user.pin_hash):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1

        if user.failed_login_attempts >= 5:
            user.locked_until = datetime.utcnow() + timedelta(minutes=15)
            db.commit()
            log_security_event(db, "account_locked", request, user.id, "5 tentatives échouées")
            raise HTTPException(403, "Trop de tentatives échouées. Compte verrouillé pendant 15 minutes.")

        db.commit()
        log_security_event(db, "login_failed", request, user.id,
                           f"Tentative {user.failed_login_attempts}/5")
        tentatives_restantes = 5 - user.failed_login_attempts
        raise HTTPException(401, f"Numéro ou PIN incorrect. {tentatives_restantes} tentative(s) restante(s).")
        
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    log_security_event(db, "login_success", request, user.id, f"Rôle: {user.role}")

    return {
    "access_token": create_access_token(user.id, user.role),
    "refresh_token": create_refresh_token(user.id, user.role),
    "token_type": "bearer",
    "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    "user": {
        "id": user.id,
        "name": user.full_name,
        "role": user.role,
        "phone_number": user.phone_number,
        "staff_function": user.staff_function,  # nouveau — utilisé par admin.html
        "org_name": user.org_name,              # nouveau — déjà attendu par d'autres pages
    },
}


# ── REFRESH ─────────────────────────────────────────────────────
@router.post("/auth/refresh")
@limiter.limit("10/minute")
def refresh_access_token(request: Request, data: RefreshTokenRequest, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(data.refresh_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(401, "Type de token invalide")

        user_id = int(payload["sub"])
        role = payload.get("role")
        user = db.query(User).filter(User.id == user_id).first()

        if not user or not user.is_active or role != user.role:
            raise HTTPException(401, "Accès refusé")

        return {
            "access_token": create_access_token(user_id, role),
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }
    except JWTError:
        raise HTTPException(401, "Refresh token invalide")


# ── PROFIL ──────────────────────────────────────────────────────
@router.get("/auth/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "name": current_user.full_name,
        "phone": current_user.phone_number,
        "phone_number": current_user.phone_number,  # alias pour cohérence front
        "email": current_user.email,
        "role": current_user.role,
        "org_name": current_user.org_name,
        "org_type": current_user.org_type,
        "staff_function": current_user.staff_function,
        "commission_rate": current_user.commission_rate,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at,
    }


@router.put("/auth/me")
def update_profile(data: UserUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    from sqlalchemy import func
    payload = data.model_dump(exclude_none=True)

    # Anti-collision email (Chantier 27 : index unique LOWER(email))
    new_email = payload.get("email")
    if new_email and new_email.lower() != (current_user.email or "").lower():
        clash = (
            db.query(User)
            .filter(func.lower(User.email) == new_email.lower(), User.id != current_user.id)
            .first()
        )
        if clash:
            raise HTTPException(409, "Cet email est déjà utilisé par un autre compte")

    for k, v in payload.items():
        setattr(current_user, k, v)
    db.commit()
    return {"message": "Profil mis à jour"}


@router.put("/auth/pin")
def change_pin(data: PinChange, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    if not verify_pin(data.old_pin, current_user.pin_hash):
        raise HTTPException(400, "Ancien PIN incorrect")
    if len(data.new_pin) != 4 or not data.new_pin.isdigit():
        raise HTTPException(400, "Le nouveau PIN doit être 4 chiffres")
    current_user.pin_hash = hash_pin(data.new_pin)
    db.commit()
    return {"message": "PIN modifié avec succès"}


# ── MOT DE PASSE OUBLIÉ / RESET PIN ─────────────────────────────
@router.post("/auth/forgot-password")
@limiter.limit("3/hour")
def forgot_password(request: Request, data: dict, db: Session = Depends(get_db)):
    email = data.get("email")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Cet email n'existe pas")

    code = "".join(random.choices(string.ascii_uppercase, k=6))

    db.query(PasswordReset).filter(PasswordReset.email == email).delete()
    new_reset = PasswordReset(email=email, code=code)
    db.add(new_reset)
    db.commit()

    envoyer_mail_code(email, code)
    return {"status": "success", "message": "Code envoyé par email"}


@router.post("/auth/reset-password")
def reset_password(data: dict, db: Session = Depends(get_db)):
    email = data.get("email")
    code = data.get("code", "").upper()
    new_pin = data.get("new_password")

    reset_entry = db.query(PasswordReset).filter(
        PasswordReset.email == email,
        PasswordReset.code == code,
    ).first()
    if not reset_entry:
        raise HTTPException(status_code=400, detail="Code incorrect ou expiré")

    temps_ecoule = datetime.utcnow() - reset_entry.created_at
    if temps_ecoule.total_seconds() > 900:
        db.delete(reset_entry)
        db.commit()
        raise HTTPException(status_code=400, detail="Code expiré (validité 15min)")

    if not new_pin or len(new_pin) != 4 or not new_pin.isdigit():
        raise HTTPException(status_code=400, detail="Le nouveau PIN doit contenir exactement 4 chiffres")

    user = db.query(User).filter(User.email == email).first()
    user.pin_hash = hash_pin(new_pin)

    db.delete(reset_entry)
    db.commit()

    return {"status": "success", "message": "Code PIN réinitialisé avec succès"}
