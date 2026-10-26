"""
core/limiter.py
Instance unique du rate limiter, partagée entre main.py et tous les routeurs.
Une seule instance = un seul compteur cohérent.

Clés (lot 2.1) — en RCA, beaucoup d'abonnés mobiles sortent par la même IP (CGNAT)
et toute une équipe d'agents partage le Wi-Fi de la salle :
  ip_key          IP réelle (core.client_ip) — défaut
  user_key        routes authentifiées : id de l'utilisateur du JWT (signature vérifiée,
                  sinon repli IP : un jeton forgé ne permet pas d'esquiver la limite)
  path_token_key  pages publiques à jeton (/invitations/pass/{token}…) : par jeton
  qr_hash_key     /ticket/view?qr_hash=… : par billet
Les routes publiques à jeton empilent une limite par jeton ET une limite IP haute.

⚠ slowapi compte par CHEMIN d'URL (request.url.path), pas par route : sur une route
à paramètre (/invitations/pass/{token}), une limite simple est donc déjà « par jeton ».
Un plafond commun à tous les chemins exige limiter.shared_limit(..., scope=…).
"""
import hashlib

from fastapi import HTTPException
from jose import JWTError, jwt
from limits import parse as parse_limit
from slowapi import Limiter
from starlette.requests import Request

from core.client_ip import client_ip
from core.config import settings


def ip_key(request: Request) -> str:
    return "ip:" + client_ip(request)


def user_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        try:
            payload = jwt.decode(auth[7:].strip(), settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            if payload.get("type") == "access" and payload.get("sub"):
                return "u:%s" % payload["sub"]
        except (JWTError, ValueError):
            pass
    return ip_key(request)


def _digest(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:32]


def path_token_key(request: Request) -> str:
    token = str(request.path_params.get("token") or "")
    return "tk:" + _digest(token) if token else ip_key(request)


def qr_hash_key(request: Request) -> str:
    qr = request.query_params.get("qr_hash") or ""
    return "qr:" + _digest(qr) if qr else ip_key(request)


limiter = Limiter(key_func=ip_key)


def hit_or_429(limit: str, scope: str, identity: str, message: str) -> None:
    """Compteur dans la route, pour une clé lue dans le CORPS de la requête (numéro,
    email, jeton de refresh) que slowapi ne sait pas lire. Même stockage, même reset."""
    if not limiter.enabled or not identity:
        return
    if not limiter.limiter.hit(parse_limit(limit), "body", scope, _digest(identity)):
        raise HTTPException(status_code=429, detail=message)

# Plafond IP COMMUN aux pages publiques à jeton (pass, calendrier, suivi, billet) :
# large (salle entière, CGNAT), anti-aspiration seulement. À utiliser avec
# limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages").
PUBLIC_IP_CEILING = "600/minute"
