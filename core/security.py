"""
core/security.py
Hash PIN, JWT (access + refresh), dépendances d'authentification, gestion de session.
"""
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_db
from models.user import User, SecurityLog

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


# ── HASH PIN ────────────────────────────────────────────────────
def hash_pin(pin: str) -> str:
    return pwd_context.hash(pin[:72])


def verify_pin(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain[:72], hashed)


# ── LOG SÉCURITÉ ────────────────────────────────────────────────
def log_security_event(
    db: Session,
    event_type: str,
    request: Request = None,
    user_id: int = None,
    details: str = None,
):
    """Enregistre un événement de sécurité dans la base."""
    try:
        ip = None
        user_agent = None
        if request:
            ip = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent", "")[:500]

        log = SecurityLog(
            event_type=event_type,
            user_id=user_id,
            ip_address=ip,
            user_agent=user_agent,
            details=details,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        print(f"⚠️ Erreur log sécurité : {e}")
        db.rollback()


# ── JWT ─────────────────────────────────────────────────────────
def create_access_token(user_id: int, role: str) -> str:
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "type": "access",
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def create_refresh_token(user_id: int, role: str) -> str:
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "type": "refresh",
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


# ── SESSION MANAGER ─────────────────────────────────────────────
class SessionManager:
    def __init__(self, db: Session):
        self.db = db

    def get_client_ip(self, request: Request) -> str:
        if request.headers.get("x-forwarded-for"):
            return request.headers.get("x-forwarded-for").split(",")[0].strip()
        return request.client.host

    def verify_session(self, user_id: int, request: Request) -> bool:
        current_ip = self.get_client_ip(request)
        last_log = (
            self.db.query(SecurityLog)
            .filter(SecurityLog.user_id == user_id)
            .order_by(SecurityLog.created_at.desc())
            .first()
        )

        if last_log and last_log.ip_address and last_log.ip_address != current_ip:
            time_since_last = datetime.utcnow() - last_log.created_at
            if time_since_last.total_seconds() < 2:
                return False
            log_security_event(
                self.db, "ip_change", request, user_id,
                f"IP changée: {last_log.ip_address} -> {current_ip}",
            )
        return True


# ── DÉPENDANCES AUTH ────────────────────────────────────────────
def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Token manquant")
    try:
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Type de token invalide.")

        user_id = int(payload.get("sub"))
        user = db.query(User).filter(User.id == user_id).first()

        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Utilisateur introuvable ou inactif")

        session_mgr = SessionManager(db)
        if not session_mgr.verify_session(user.id, request):
            raise HTTPException(status_code=401, detail="Changement d'IP suspect.")

        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expiré ou invalide")


def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User | None:
    if not credentials:
        return None
    try:
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return db.query(User).filter(User.id == int(payload["sub"])).first()
    except JWTError:
        return None
