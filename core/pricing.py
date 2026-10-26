"""
core/pricing.py — Source unique des tarifs GoEvent.

Valeurs par défaut = constantes de core/config.py ; les tarifs en vigueur sont
réglés par l'admin (back-office → Tarifs) et stockés dans site_settings["pricing"].
Lus par :
  - api/promotion.py (montants RÉELLEMENT facturés : Boost, Pack Facebook) ;
  - GET /pricing → web/cms.js (prix AFFICHÉS : page Partenaires, dashboard orga,
    simulateur, paiement, admin). Affiché = facturé, par construction.

La commission billetterie est exposée en lecture seule : elle engage les CGU,
elle ne se modifie que dans core/config.py (et les CGU).
"""
import copy
import json
import time

from sqlalchemy.orm import Session

from core.config import (
    BOOST_PACKAGES, COMMISSION_BUYER_RATE, COMMISSION_ORGANIZER_RATE,
    FB_PACK_MARGIN_RATE, FB_PACK_MIN_BUDGET, INVITATION_PACK_SIZES,
)

DEFAULT_PRICING = {
    # Boost organisateur : durées fixes, montants réglables
    "boost": {code: {"days": p["duration_days"], "amount": p["amount_fcfa"]} for code, p in BOOST_PACKAGES.items()},
    "fb_pack": {"min_budget": FB_PACK_MIN_BUDGET, "margin_pct": int(round(FB_PACK_MARGIN_RATE * 100))},
    # Slot Hero (carrousel d'accueil) : vendu par contrat (Régie pub)
    "slot_hero": {"1w": 50_000, "1m": 150_000, "3m": 500_000},
    # Emplacements publicitaires (par mois). None = « Sur devis »
    "ad_formats": {"home_banner": None, "events_sidebar": None, "events_infeed": None,
                   "event_detail": None, "fan_space": None},
    # Services premium (pas encore en vente : affichage uniquement)
    "premium": {"qr_custom": 5_000, "gift_tickets_from": 15_000, "report": 10_000, "printable": 5_000},
    # Invitations digitales : taille du pack (personnes) × niveau de service.
    # Valeurs PROVISOIRES. Montant figé sur le pack à son activation.
    "invitations": {
        "autonome_25": 10_000, "autonome_50": 15_000, "autonome_100": 25_000, "autonome_300": 60_000,
        "autonome_500": 90_000, "autonome_1000": 150_000, "autonome_2000": 250_000,
        "accompagne_25": 30_000, "accompagne_50": 40_000, "accompagne_100": 55_000, "accompagne_300": 110_000,
        "accompagne_500": 150_000, "accompagne_1000": 250_000, "accompagne_2000": 400_000,
    },
}

LABELS = {
    "boost.7d": "Boost 7 jours", "boost.30d": "Boost 30 jours", "boost.90d": "Boost 90 jours",
    "fb_pack.min_budget": "Pack Facebook : budget minimum", "fb_pack.margin_pct": "Pack Facebook : marge GoEvent (%)",
    "slot_hero.1w": "Slot Hero : 1 semaine", "slot_hero.1m": "Slot Hero : 1 mois", "slot_hero.3m": "Slot Hero : 3 mois",
    "ad_formats.home_banner": "Bannière accueil (mois)", "ad_formats.events_sidebar": "Colonne Événements (mois)",
    "ad_formats.events_infeed": "Carte sponsorisée dans la liste (mois)",
    "ad_formats.event_detail": "Fiche événement (mois)", "ad_formats.fan_space": "Espace fan (mois)",
    "premium.qr_custom": "QR Code personnalisé", "premium.gift_tickets_from": "Tickets cadeaux (dès)",
    "premium.report": "Rapport post-event", "premium.printable": "Billets imprimables",
    **{"invitations.%s_%d" % (svc, n): "Invitations %s : %d personnes" % ("Autonome" if svc == "autonome" else "Accompagné", n)
       for svc in ("autonome", "accompagne") for n in INVITATION_PACK_SIZES},
}

MAX_AMOUNT = 10_000_000
_CACHE = {"t": 0.0, "v": None}
_TTL = 30.0


def _merge(defaults: dict, stored: dict) -> dict:
    """Ne retient du stocké que les clés connues (un vieux réglage ne casse rien)."""
    out = copy.deepcopy(defaults)
    for group, values in (stored or {}).items():
        if group not in out or not isinstance(values, dict):
            continue
        for key, val in values.items():
            if key not in out[group]:
                continue
            if isinstance(out[group][key], dict):  # boost : {days, amount} → seul amount est réglable
                if isinstance(val, dict) and isinstance(val.get("amount"), int):
                    out[group][key]["amount"] = val["amount"]
            elif val is None or isinstance(val, int):
                out[group][key] = val
    return out


def get_pricing(db: Session, fresh: bool = False) -> dict:
    from models.site_content import SiteSetting
    now = time.monotonic()
    if not fresh and _CACHE["v"] is not None and now - _CACHE["t"] < _TTL:
        return copy.deepcopy(_CACHE["v"])
    row = db.query(SiteSetting).filter(SiteSetting.key == "pricing").first()
    stored = {}
    if row:
        try:
            stored = json.loads(row.value)
        except ValueError:
            stored = {}
    merged = _merge(DEFAULT_PRICING, stored)
    _CACHE.update(t=now, v=merged)
    return copy.deepcopy(merged)


def invalidate_cache():
    _CACHE.update(t=0.0, v=None)


def public_pricing(db: Session) -> dict:
    p = get_pricing(db)
    p["commission"] = {"buyer_pct": round(COMMISSION_BUYER_RATE * 100, 2),
                       "organizer_pct": round(COMMISSION_ORGANIZER_RATE * 100, 2),
                       "editable": False}
    return p


def validate_update(payload: dict) -> dict:
    """Valide un payload {group: {key: montant|None}} → dict prêt à stocker. Lève ValueError."""
    clean = {}
    for group, values in (payload or {}).items():
        if group not in DEFAULT_PRICING or not isinstance(values, dict):
            raise ValueError("Groupe de tarifs inconnu : %s" % group)
        clean[group] = {}
        for key, val in values.items():
            if key not in DEFAULT_PRICING[group]:
                raise ValueError("Tarif inconnu : %s.%s" % (group, key))
            label = LABELS.get("%s.%s" % (group, key), key)
            if isinstance(val, dict):
                val = val.get("amount")
            if val is None:
                if group != "ad_formats":
                    raise ValueError("« %s » est obligatoire." % label)
                clean[group][key] = None
                continue
            if isinstance(val, bool) or not isinstance(val, (int, float)) or int(val) != val:
                raise ValueError("« %s » : montant entier attendu." % label)
            val = int(val)
            if group == "fb_pack" and key == "margin_pct":
                if not 0 <= val <= 100:
                    raise ValueError("Marge : entre 0 et 100 %.")
            elif not 1 <= val <= MAX_AMOUNT:
                raise ValueError("« %s » : entre 1 et %d FCFA." % (label, MAX_AMOUNT))
            clean[group][key] = {"amount": val} if group == "boost" else val
    return clean
