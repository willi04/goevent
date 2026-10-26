"""
core/client_ip.py — IP réelle du client derrière Render.

uvicorn --proxy-headers --forwarded-allow-ips='*' remplace request.client.host par
l'entrée la PLUS À GAUCHE de X-Forwarded-For : c'est la valeur envoyée par le client,
donc falsifiable (un attaquant change d'« IP » à chaque requête et contourne les
limites). On lit les en-têtes nous-mêmes, selon CLIENT_IP_STRATEGY (variable Render,
fixée après diagnostic : GET /admin/diagnostics/client-ip, cf. docs/07) :

  peer            request.client.host (comportement historique — défaut, aucun changement)
  header:<nom>    en-tête posé par le proxy de Render (ex. header:true-client-ip,
                  header:cf-connecting-ip) — seulement si Render l'écrase toujours
  xff:<n>         n-ième entrée de X-Forwarded-For en partant de la DROITE
                  (1 = dernière, ajoutée par le dernier proxy ; 2 = celle qu'il a reçue…)

Valeur illisible ou absente → repli sur peer (jamais d'exception).
"""
import ipaddress
from typing import Optional

from fastapi import Request

from core.config import settings

_PROBE_HEADERS = ("true-client-ip", "cf-connecting-ip", "x-real-ip", "x-client-ip", "fastly-client-ip")


def _valid(v: Optional[str]) -> Optional[str]:
    v = (v or "").split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(v)) if v else None
    except ValueError:
        return None


def _xff(request: Request) -> list:
    return [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def resolve(request: Request, strategy: str) -> Optional[str]:
    s = (strategy or "peer").strip().lower()
    if s.startswith("header:"):
        return _valid(request.headers.get(s[7:]))
    if s.startswith("xff:"):
        try:
            n = int(s[4:])
        except ValueError:
            return None
        parts = _xff(request)
        return _valid(parts[-n]) if 1 <= n <= len(parts) else None
    return _peer(request)


def client_ip(request: Request) -> str:
    return resolve(request, settings.CLIENT_IP_STRATEGY) or _peer(request)


def diagnostics(request: Request) -> dict:
    """Pour choisir la stratégie : comparer les candidats à l'IP réelle, avec et sans
    en-têtes falsifiés par le client. Le bon candidat ne change jamais."""
    parts = _xff(request)
    cands = {"peer": _peer(request)}
    for h in _PROBE_HEADERS:
        cands["header:" + h] = _valid(request.headers.get(h))
    for n in range(1, min(len(parts), 5) + 1):
        cands["xff:%d" % n] = _valid(parts[-n])
    return {
        "strategy": settings.CLIENT_IP_STRATEGY,
        "client_ip": client_ip(request),
        "x_forwarded_for": parts,
        "headers": {k: v for k, v in request.headers.items()
                    if k in _PROBE_HEADERS or k in ("forwarded", "x-forwarded-for", "x-forwarded-proto")
                    or k.startswith(("cf-", "x-render"))},
        "candidates": cands,
    }
