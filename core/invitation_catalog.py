"""
core/invitation_catalog.py — Catalogue des packs Invitations (lot 2.2).

Source unique des formules, packs, options, codes de remise et réglages (TVA,
plafond de remise manuelle). Lu par :
  - l'aperçu (devis) et l'activation d'un pack : un SEUL calcul, quote() →
    affiché = facturé ;
  - GET /invitations/catalog (page publique, lot 3) et le futur tableau de bord
    organisateur.

Règles
- Une seule remise par commande : un code OU une remise manuelle (admin, motif,
  plafond réglable). La remise porte sur le PACK uniquement (jamais sur les
  options) et ne dépasse jamais son prix.
- Total à 0 : uniquement « Pack offert » (admin, motif, canal « offert », sans
  référence de paiement), jamais confondu avec un paiement.
- Sur mesure : taille et prix libres, admin, motif obligatoire.
- La commande garde un instantané complet : modifier / archiver le catalogue
  ne la change jamais. Un élément utilisé est archivable, pas supprimable.
- Commandes antérieures au catalogue (colonnes NULL) : lues comme « ancien
  pack » (comportements déduits du code accompagne / autonome).
"""
import json
import math
import secrets
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.security import log_security_event
from models.invitation import InvitationPack
from models.invitation_catalog import (
    InvCatalogLog, InvDiscountCode, InvFormula, InvOption, InvPack, inv_option_formulas,
)

SETTINGS_KEY = "invitations_catalog"
DEFAULT_SETTINGS = {"manual_discount_max_pct": 30, "vat_enabled": False, "vat_rate_bp": 0, "prices_include_vat": True}
MAX_SIZE = 5000
MAX_PRICE = 50_000_000
MAX_QTY = 1000
UNITS = {"flat": "forfait", "per_unit": "par unité", "per_page": "par page"}
FEATURES = ("onsite_agents", "track_link", "team_sends")
OFFERED_CHANNEL = "offert"

# Reprise de l'existant (lot 2.1) : formules, tailles et prix par défaut d'avant le catalogue
LEGACY_SIZES = (25, 50, 100, 300, 500, 1000, 2000)
LEGACY_PRICES = {
    "autonome": {25: 10_000, 50: 15_000, 100: 25_000, 300: 60_000, 500: 90_000, 1000: 150_000, 2000: 250_000},
    "accompagne": {25: 30_000, 50: 40_000, 100: 55_000, 300: 110_000, 500: 150_000, 1000: 250_000, 2000: 400_000},
}
LEGACY_FEATURES = {
    "autonome": {"onsite_agents": False, "track_link": False, "team_sends": False},
    "accompagne": {"onsite_agents": True, "track_link": True, "team_sends": True},
}
LEGACY_AGENTS_STEP = 300


def utcnow() -> datetime:
    return datetime.utcnow()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# ════════════════════════════════════════════════════════════
# Réglages (TVA, plafond de remise manuelle)
# ════════════════════════════════════════════════════════════
def get_settings(db: Session) -> dict:
    from models.site_content import SiteSetting
    row = db.query(SiteSetting).filter(SiteSetting.key == SETTINGS_KEY).first()
    out = dict(DEFAULT_SETTINGS)
    if row:
        try:
            stored = json.loads(row.value)
            out.update({k: stored[k] for k in DEFAULT_SETTINGS if k in stored})
        except ValueError:
            pass
    return out


def save_settings(db: Session, data: dict, user_id: int, request=None) -> dict:
    from models.site_content import SiteSetting
    before = get_settings(db)
    after = dict(before)
    after.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS and v is not None})
    if not 0 <= int(after["manual_discount_max_pct"]) <= 100:
        raise HTTPException(400, "Plafond de remise manuelle : entre 0 et 100 %.")
    if not 0 <= int(after["vat_rate_bp"]) <= 5000:
        raise HTTPException(400, "Taux de TVA : entre 0 et 50 %.")
    if after["vat_enabled"] and int(after["vat_rate_bp"]) <= 0:
        raise HTTPException(400, "Indiquez le taux de TVA avant de l'activer.")
    row = db.query(SiteSetting).filter(SiteSetting.key == SETTINGS_KEY).first()
    if not row:
        row = SiteSetting(key=SETTINGS_KEY, value="{}")
        db.add(row)
    row.value, row.updated_by = json.dumps(after), user_id
    changes = diff(before, after)
    if changes:
        log_change(db, "settings", None, "update", "Réglages du catalogue", changes, user_id)
    db.commit()
    if changes:
        log_security_event(db, "invitation_catalog_settings", request, user_id, _changes_txt(changes))
    return after


# ════════════════════════════════════════════════════════════
# Historique
# ════════════════════════════════════════════════════════════
def _plain(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def diff(before: dict, after: dict) -> dict:
    return {k: [_plain(before.get(k)), _plain(after.get(k))] for k in after
            if _plain(before.get(k)) != _plain(after.get(k))}


def _changes_txt(changes: dict) -> str:
    return "; ".join("%s: %s → %s" % (k, v[0], v[1]) for k, v in changes.items())[:900]


def log_change(db: Session, entity: str, entity_id: Optional[int], action: str, label: str, changes: dict,
               user_id: Optional[int]):
    db.add(InvCatalogLog(entity=entity, entity_id=entity_id, action=action, label=(label or "")[:120],
                         changes=changes or {}, user_id=user_id))


# ════════════════════════════════════════════════════════════
# Sérialisation
# ════════════════════════════════════════════════════════════
def formula_fields(f: InvFormula) -> dict:
    return {"code": f.code, "name": f.name, "description": f.description, "included": list(f.included or []),
            "badge": f.badge, "public": f.public, "onsite_agents": f.onsite_agents, "track_link": f.track_link,
            "team_sends": f.team_sends, "agents_step": f.agents_step, "sort_order": f.sort_order, "status": f.status}


def formula_out(f: InvFormula) -> dict:
    return dict(formula_fields(f), id=f.id, used=f.used)


def pack_fields(p: InvPack) -> dict:
    return {"formula_id": p.formula_id, "size": p.size, "price_fcfa": p.price_fcfa, "on_quote": p.on_quote,
            "name": p.name, "public": p.public, "sort_order": p.sort_order, "valid_from": p.valid_from,
            "valid_until": p.valid_until, "status": p.status}


def pack_out(p: InvPack, now: Optional[datetime] = None) -> dict:
    d = {k: _plain(v) for k, v in pack_fields(p).items()}
    d.update(id=p.id, used=p.used, available=pack_available(p, now))
    return d


def option_fields(o: InvOption, formula_ids: list) -> dict:
    return {"name": o.name, "description": o.description, "price_fcfa": o.price_fcfa, "unit": o.unit,
            "unit_label": o.unit_label, "max_qty": o.max_qty, "agents_per_unit": o.agents_per_unit,
            "public": o.public, "sort_order": o.sort_order, "status": o.status, "formula_ids": sorted(formula_ids)}


def option_out(o: InvOption, formula_ids: list) -> dict:
    return dict(option_fields(o, formula_ids), id=o.id, used=o.used)


def discount_fields(d: InvDiscountCode) -> dict:
    return {"code": d.code, "kind": d.kind, "value": d.value, "max_uses": d.max_uses, "valid_from": d.valid_from,
            "valid_until": d.valid_until, "formula_ids": sorted(d.formula_ids or []),
            "pack_ids": sorted(d.pack_ids or []), "note": d.note, "status": d.status, "sort_order": d.sort_order}


def discount_out(d: InvDiscountCode, uses: int) -> dict:
    out = {k: _plain(v) for k, v in discount_fields(d).items()}
    out.update(id=d.id, used=d.used, uses=uses)
    return out


def option_formula_map(db: Session) -> dict:
    m = {}
    for oid, fid in db.query(inv_option_formulas.c.option_id, inv_option_formulas.c.formula_id).all():
        m.setdefault(oid, []).append(fid)
    return m


def code_uses(db: Session, code_id: int) -> int:
    """Utilisations = commandes ACTIVES portant le code (une annulation libère l'utilisation)."""
    return db.query(func.count(InvitationPack.id)).filter(
        InvitationPack.discount_code_id == code_id, InvitationPack.status == "actif").scalar() or 0


def pack_available(p: InvPack, now: Optional[datetime] = None) -> bool:
    now = now or utcnow()
    return (p.status == "active" and (p.valid_from is None or p.valid_from <= now)
            and (p.valid_until is None or now <= p.valid_until))


def full_catalog(db: Session, include_archived: bool = True) -> dict:
    now = utcnow()
    fq = db.query(InvFormula)
    pq = db.query(InvPack)
    oq = db.query(InvOption)
    dq = db.query(InvDiscountCode)
    if not include_archived:
        fq, pq, oq, dq = (q.filter(m.status == "active") for q, m in
                          ((fq, InvFormula), (pq, InvPack), (oq, InvOption), (dq, InvDiscountCode)))
    ofm = option_formula_map(db)
    uses = dict(db.query(InvitationPack.discount_code_id, func.count(InvitationPack.id)).filter(
        InvitationPack.discount_code_id.isnot(None), InvitationPack.status == "actif")
        .group_by(InvitationPack.discount_code_id).all())
    return {
        "formulas": [formula_out(f) for f in fq.order_by(InvFormula.sort_order, InvFormula.id).all()],
        "packs": [pack_out(p, now) for p in pq.order_by(InvPack.formula_id, InvPack.size, InvPack.id).all()],
        "options": [option_out(o, ofm.get(o.id, [])) for o in oq.order_by(InvOption.sort_order, InvOption.id).all()],
        "discounts": [discount_out(d, uses.get(d.id, 0)) for d in dq.order_by(InvDiscountCode.id.desc()).all()],
        "settings": get_settings(db), "units": UNITS,
    }


def public_catalog(db: Session) -> dict:
    """Page publique (lot 3) : formules / packs / options visibles, actifs, dans leurs dates. Jamais les codes."""
    now = utcnow()
    formulas = (db.query(InvFormula).filter(InvFormula.status == "active", InvFormula.public == True)  # noqa: E712
                .order_by(InvFormula.sort_order, InvFormula.id).all())
    fids = {f.id for f in formulas}
    packs = [p for p in db.query(InvPack).filter(InvPack.status == "active", InvPack.public == True)  # noqa: E712
             .order_by(InvPack.sort_order, InvPack.size).all() if p.formula_id in fids and pack_available(p, now)]
    ofm = option_formula_map(db)
    options = [o for o in db.query(InvOption).filter(InvOption.status == "active", InvOption.public == True)  # noqa: E712
               .order_by(InvOption.sort_order, InvOption.id).all() if set(ofm.get(o.id, [])) & fids]
    st = get_settings(db)
    return {
        "formulas": [{"id": f.id, "code": f.code, "name": f.name, "description": f.description,
                      "included": list(f.included or []), "badge": f.badge,
                      **{k: getattr(f, k) for k in FEATURES}} for f in formulas],
        "packs": [{"id": p.id, "formula_id": p.formula_id, "size": p.size, "name": p.name,
                   "price_fcfa": None if p.on_quote else p.price_fcfa, "on_quote": p.on_quote,
                   "valid_until": _iso(p.valid_until)} for p in packs],
        "options": [{"id": o.id, "name": o.name, "description": o.description, "price_fcfa": o.price_fcfa,
                     "unit": o.unit, "unit_label": o.unit_label,
                     "formula_ids": sorted(set(ofm.get(o.id, [])) & fids)} for o in options],
        "vat": {"enabled": st["vat_enabled"], "rate": st["vat_rate_bp"] / 100.0,
                "prices_include_vat": st["prices_include_vat"]},
    }


# ════════════════════════════════════════════════════════════
# Reprise de l'existant
# ════════════════════════════════════════════════════════════
def seed_catalog(db: Session) -> int:
    """Catalogue vide → Autonome + Accompagné, 7 tailles, aux prix EN VIGUEUR (défauts + surcharges
    de l'ancien onglet Tarifs). Idempotent. Retourne le nombre de packs créés."""
    if db.query(InvFormula.id).first():
        return 0
    from models.site_content import SiteSetting
    overrides = {}
    row = db.query(SiteSetting).filter(SiteSetting.key == "pricing").first()
    if row:
        try:
            overrides = (json.loads(row.value) or {}).get("invitations") or {}
        except ValueError:
            overrides = {}
    specs = [
        ("autonome", "Autonome", "Vous envoyez les pass et scannez vous-même à l'entrée.",
         ["Pass nominatifs à QR Code", "Envoi par WhatsApp en un clic", "Scanner sur téléphone, même hors ligne"], 1),
        ("accompagne", "Accompagné", "L'équipe GoEvent envoie les pass et contrôle les entrées sur place.",
         ["Envoi des pass par l'équipe GoEvent", "Agent(s) GoEvent à l'entrée", "Lien de suivi des arrivées en direct"], 2),
    ]
    n = 0
    for code, name, desc, included, order in specs:
        f = InvFormula(code=code, name=name, description=desc, included=included, public=True, sort_order=order,
                       agents_step=LEGACY_AGENTS_STEP if LEGACY_FEATURES[code]["onsite_agents"] else None,
                       **LEGACY_FEATURES[code])
        db.add(f)
        db.flush()
        log_change(db, "formula", f.id, "seed", name, {k: [None, _plain(v)] for k, v in formula_fields(f).items()}, None)
        for size in LEGACY_SIZES:
            price = overrides.get("%s_%d" % (code, size))
            if not isinstance(price, int) or isinstance(price, bool) or price < 0:
                price = LEGACY_PRICES[code][size]
            p = InvPack(formula_id=f.id, size=size, price_fcfa=price, public=True, sort_order=size)
            db.add(p)
            db.flush()
            log_change(db, "pack", p.id, "seed", "%s %d pers." % (name, size),
                       {"size": [None, size], "price_fcfa": [None, price]}, None)
            n += 1
    # Les formules des commandes déjà actives sont « utilisées » (non supprimables)
    for f in db.query(InvFormula).all():
        if db.query(InvitationPack.id).filter(InvitationPack.service == f.code).first():
            f.used = True
    db.commit()
    return n


# ════════════════════════════════════════════════════════════
# Comportements d'un événement (lus sur les commandes actives)
# ════════════════════════════════════════════════════════════
def agents_for(formula, size: int) -> int:
    if not formula.onsite_agents or not formula.agents_step:
        return 0
    return max(1, math.ceil(size / formula.agents_step))


def order_features(p: InvitationPack) -> dict:
    if p.features is not None:
        return {k: bool(p.features.get(k)) for k in FEATURES}
    return dict(LEGACY_FEATURES.get(p.service, LEGACY_FEATURES["autonome"]))


def order_agents(p: InvitationPack) -> int:
    if p.agents_included is not None:
        return p.agents_included
    return math.ceil(p.size / LEGACY_AGENTS_STEP) if p.service == "accompagne" else 0


def active_orders(db: Session, event_id: int) -> list:
    return db.query(InvitationPack).filter(InvitationPack.event_id == event_id, InvitationPack.status == "actif").all()


def event_features(db: Session, event_id: int, formula_code: Optional[str] = None) -> dict:
    """Union des comportements des commandes actives. Sans commande : ceux de la formule choisie
    pour l'événement (préparation avant paiement)."""
    orders = active_orders(db, event_id)
    if orders:
        out = {k: False for k in FEATURES}
        for p in orders:
            for k, v in order_features(p).items():
                out[k] = out[k] or v
        return out
    f = db.query(InvFormula).filter(InvFormula.code == formula_code).first() if formula_code else None
    if f:
        return {k: bool(getattr(f, k)) for k in FEATURES}
    return dict(LEGACY_FEATURES.get(formula_code or "", LEGACY_FEATURES["autonome"]))


def agents_included_total(db: Session, event_id: int) -> int:
    return sum(order_agents(p) for p in active_orders(db, event_id))


def agents_assigned(db: Session, event_id: int) -> int:
    from models.user import User
    return db.query(func.count(User.id)).filter(User.role == "agent", User.agent_event_id == event_id,
                                                User.is_active == True).scalar() or 0  # noqa: E712


def formula_label(db: Session, code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    f = db.query(InvFormula).filter(InvFormula.code == code).first()
    return f.name if f else code


def revenue_summary(db: Session) -> dict:
    """Encaissé = commandes actives PAYÉES ; les packs offerts sont comptés à part (jamais en recette)."""
    paid = db.query(func.count(InvitationPack.id), func.coalesce(func.sum(InvitationPack.amount_fcfa), 0)).filter(
        InvitationPack.status == "actif", InvitationPack.offered == False).one()  # noqa: E712
    offered = db.query(func.count(InvitationPack.id)).filter(
        InvitationPack.status == "actif", InvitationPack.offered == True).scalar() or 0  # noqa: E712
    return {"paid_count": int(paid[0]), "paid_total_fcfa": int(paid[1]), "offered_count": int(offered)}


# ════════════════════════════════════════════════════════════
# Devis (quote) — calcul UNIQUE pour l'aperçu et l'activation
# ════════════════════════════════════════════════════════════
def _reason(v, what: str) -> str:
    v = " ".join(str(v or "").split())
    if len(v) < 3:
        raise HTTPException(400, "Motif obligatoire (%s)." % what)
    return v[:300]


def _int(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= int(v) <= hi:
        raise HTTPException(400, "%s : nombre entier entre %d et %d attendu." % (what, lo, hi))
    return int(v)


def vat_breakdown(total: int, st: dict) -> dict:
    bp = int(st["vat_rate_bp"]) if st["vat_enabled"] else 0
    if not bp:
        return {"enabled": False, "rate_bp": 0, "prices_include_vat": st["prices_include_vat"],
                "ht": total, "vat": 0, "ttc": total}
    if st["prices_include_vat"]:
        ht = int(round(total * 10000 / (10000 + bp)))
        return {"enabled": True, "rate_bp": bp, "prices_include_vat": True, "ht": ht, "vat": total - ht, "ttc": total}
    vat = int(round(total * bp / 10000))
    return {"enabled": True, "rate_bp": bp, "prices_include_vat": False, "ht": total, "vat": vat, "ttc": total + vat}


def quote(db: Session, ev, admin: bool, data: dict, lock_code: bool = False) -> dict:
    """data : formula_id, pack_id | custom {size, price_fcfa, reason}, options [{option_id, qty}],
    discount_code | manual_discount {kind, value, reason}, offered (bool) + offered_reason."""
    from core.invitations import quota, seats_listed
    now = utcnow()
    st = get_settings(db)
    f = db.query(InvFormula).filter(InvFormula.id == data.get("formula_id")).first()
    if not f or f.status != "active":
        raise HTTPException(400, "Formule introuvable ou archivée.")
    custom = data.get("custom")
    offered = bool(data.get("offered"))
    pack = None
    if custom:
        if not admin:
            raise HTTPException(403, "Pack sur mesure : réservé à l'administrateur.")
        size = _int(custom.get("size"), 1, MAX_SIZE, "Taille")
        unit_price = _int(custom.get("price_fcfa"), 0, MAX_PRICE, "Prix")
        custom_reason = _reason(custom.get("reason"), "pack sur mesure")
        pack_name = "Sur mesure"
    else:
        pack = db.query(InvPack).filter(InvPack.id == data.get("pack_id")).first()
        if not pack or pack.formula_id != f.id:
            raise HTTPException(400, "Choisissez un pack de cette formule.")
        if not pack_available(pack, now):
            raise HTTPException(400, "Ce pack n'est pas disponible (archivé ou hors de ses dates).")
        if pack.on_quote or pack.price_fcfa is None:
            raise HTTPException(400, "Pack « sur devis » : utilisez « Sur mesure » avec le prix convenu.")
        size, unit_price, custom_reason = pack.size, pack.price_fcfa, None
        pack_name = pack.name

    # Options (jamais remisées : elles ont un coût réel)
    lines, options_total, extra_agents = [], 0, 0
    allowed = {oid for (oid,) in db.query(inv_option_formulas.c.option_id)
               .filter(inv_option_formulas.c.formula_id == f.id).all()}
    seen = set()
    for item in data.get("options") or []:
        oid = item.get("option_id")
        if oid in seen:
            raise HTTPException(400, "Option en double.")
        seen.add(oid)
        o = db.query(InvOption).filter(InvOption.id == oid).first()
        if not o or o.status != "active" or o.id not in allowed:
            raise HTTPException(400, "Option indisponible pour cette formule.")
        qty = 1 if o.unit == "flat" else _int(item.get("qty"), 0, o.max_qty, "Quantité « %s »" % o.name)
        if qty == 0:
            continue
        total_line = o.price_fcfa * qty
        options_total += total_line
        extra_agents += qty * (o.agents_per_unit or 0)
        lines.append({"option_id": o.id, "name": o.name, "unit": o.unit, "unit_label": o.unit_label,
                      "unit_price": o.price_fcfa, "qty": qty, "total": total_line,
                      "agents_per_unit": o.agents_per_unit or 0})

    # Une seule remise : code OU manuelle, sur le pack uniquement
    code_txt = (data.get("discount_code") or "").strip().upper()
    manual = data.get("manual_discount")
    if offered and (code_txt or manual):
        raise HTTPException(400, "Pack offert : aucune remise à appliquer.")
    if code_txt and manual:
        raise HTTPException(400, "Une seule remise par commande : un code OU une remise manuelle.")
    discount = None
    if code_txt:
        q = db.query(InvDiscountCode).filter(InvDiscountCode.code == code_txt)
        dc = (q.with_for_update() if lock_code else q).first()
        if not dc or dc.status != "active":
            raise HTTPException(400, "Code de remise inconnu ou désactivé.")
        if (dc.valid_from and now < dc.valid_from) or (dc.valid_until and now > dc.valid_until):
            raise HTTPException(400, "Code de remise hors de sa période de validité.")
        if dc.formula_ids and f.id not in dc.formula_ids:
            raise HTTPException(400, "Ce code ne s'applique pas à cette formule.")
        if dc.pack_ids and (pack is None or pack.id not in dc.pack_ids):
            raise HTTPException(400, "Ce code ne s'applique pas à ce pack.")
        if dc.max_uses and code_uses(db, dc.id) >= dc.max_uses:
            raise HTTPException(400, "Ce code a atteint son nombre maximal d'utilisations.")
        amount = (unit_price * dc.value + 50) // 100 if dc.kind == "pct" else dc.value
        discount = {"kind": "code", "code_id": dc.id, "code": dc.code, "type": dc.kind, "value": dc.value,
                    "amount": min(amount, unit_price), "reason": None}
    elif manual:
        if not admin:
            raise HTTPException(403, "Remise manuelle : réservée à l'administrateur.")
        kind = manual.get("kind")
        if kind not in ("pct", "fixed"):
            raise HTTPException(400, "Remise manuelle : pourcentage ou montant.")
        value = _int(manual.get("value"), 1, 100 if kind == "pct" else MAX_PRICE, "Remise manuelle")
        reason = _reason(manual.get("reason"), "remise manuelle")
        amount = min((unit_price * value + 50) // 100 if kind == "pct" else value, unit_price)
        cap_pct = int(st["manual_discount_max_pct"])
        cap = unit_price * cap_pct // 100
        if amount > cap:
            raise HTTPException(400, "Remise manuelle plafonnée à %d %% du pack (%d FCFA maximum)." % (cap_pct, cap))
        discount = {"kind": "manual", "code_id": None, "code": None, "type": kind, "value": value,
                    "amount": amount, "reason": reason}

    offered_reason = _reason(data.get("offered_reason"), "pack offert") if offered else None
    if offered and not admin:
        raise HTTPException(403, "Pack offert : réservé à l'administrateur.")
    subtotal = unit_price + options_total
    total = 0 if offered else subtotal - (discount["amount"] if discount else 0)
    if total == 0 and not offered:
        raise HTTPException(400, "Total nul : utilisez « Pack offert » (motif obligatoire).")
    vat = vat_breakdown(total, st)

    features = {k: bool(getattr(f, k)) for k in FEATURES}
    agents_inc = agents_for(f, size) + extra_agents
    existing_agents = agents_included_total(db, ev.id)
    assigned = agents_assigned(db, ev.id)
    listed, quota_now = seats_listed(db, ev.id), quota(db, ev.id)
    warnings, blocking = [], []
    total_inc = existing_agents + agents_inc
    # Avertissement (jamais bloquant) : plus d'agents GoEvent assignés que d'agents inclus.
    # Formule sans agents sur place (Autonome) : les agents sont ceux du client, pas de quota.
    onsite = features["onsite_agents"] or any(order_features(o)["onsite_agents"] for o in active_orders(db, ev.id))
    if onsite and assigned > total_inc:
        warnings.append("%d agent(s) déjà assigné(s) pour %d inclus : proposez l'option « agent supplémentaire »."
                        % (assigned, total_inc))
    if quota_now + size < listed:
        blocking.append("Pack trop petit : %d personnes déjà dans la liste (quota après activation : %d)."
                        % (listed, quota_now + size))
    return {
        "formula": {"id": f.id, "code": f.code, "name": f.name},
        "pack": {"id": pack.id if pack else None, "name": pack_name, "size": size, "unit_price": unit_price,
                 "custom": bool(custom), "custom_reason": custom_reason},
        "options": lines, "options_total": options_total, "discount": discount,
        "subtotal": subtotal, "total": total, "vat": vat, "amount_due": vat["ttc"],
        "offered": offered, "offered_reason": offered_reason, "features": features,
        "agents_included": agents_inc, "agents_included_total": total_inc, "agents_assigned": assigned,
        "listed": listed, "quota_after": quota_now + size, "warnings": warnings, "blocking": blocking,
    }


def activate(db: Session, ev, user, admin: bool, data: dict, channels: list, request=None) -> InvitationPack:
    """Activation = commande figée. L'événement est verrouillé par la route ; le code de remise est
    verrouillé ici (FOR UPDATE) pour que deux activations simultanées ne dépassent pas max_uses."""
    import re
    q = quote(db, ev, admin, data, lock_code=True)
    if q["blocking"]:
        raise HTTPException(400, q["blocking"][0])
    if q["offered"]:
        channel, ref, received, confirmed = OFFERED_CHANNEL, "OFFERT-%d-%s" % (ev.id, secrets.token_hex(4).upper()), 0, False
    else:
        channel = data.get("payment_channel")
        ch = next((c for c in channels if c["code"] == channel and c["active"]), None)
        if not ch:
            raise HTTPException(400, "Moyen de paiement non disponible.")
        if not data.get("confirmed_goevent_account"):
            raise HTTPException(400, "Confirmez que le paiement a été reçu sur un moyen au nom de GoEvent SARLU.")
        ref = re.sub(r"\s+", " ", str(data.get("payment_ref") or "").strip())
        if len(ref) < 3:
            raise HTTPException(400, "Référence du paiement obligatoire (%s)." % ch["ref_label"])
        received = data.get("amount_received")
        if isinstance(received, bool) or not isinstance(received, int) or received < 0:
            raise HTTPException(400, "Montant reçu invalide.")
        if received < q["amount_due"]:
            raise HTTPException(400, "Montant reçu (%d FCFA) inférieur au total à payer (%d FCFA)."
                                % (received, q["amount_due"]))
        if db.query(InvitationPack.id).filter(InvitationPack.payment_channel == channel,
                                              func.upper(InvitationPack.payment_ref) == ref.upper()).first():
            raise HTTPException(409, "Cette référence de paiement a déjà servi à activer un pack.")
        confirmed = True
    d = q["discount"]
    snapshot = {
        "formula": q["formula"], "pack": q["pack"], "options": q["options"], "discount": d,
        "offered_reason": q["offered_reason"], "vat": q["vat"], "subtotal": q["subtotal"], "total": q["total"],
        "amount_due": q["amount_due"], "features": q["features"], "agents_included": q["agents_included"],
        "catalog_at": utcnow().isoformat(),
    }
    note = (data.get("note") or "").strip()[:300] or None
    p = InvitationPack(
        event_id=ev.id, size=q["pack"]["size"], service=q["formula"]["code"], amount_fcfa=q["amount_due"],
        payment_channel=channel, payment_ref=ref[:80], amount_received=received, confirmed_goevent_account=confirmed,
        activated_by=user.id, note=note,
        formula_id=q["formula"]["id"], formula_name=q["formula"]["name"], catalog_pack_id=q["pack"]["id"],
        custom=q["pack"]["custom"], offered=q["offered"], unit_price=q["pack"]["unit_price"],
        options_total=q["options_total"], discount_kind=d["kind"] if d else None,
        discount_code_id=d["code_id"] if d else None, discount_amount=d["amount"] if d else 0,
        agents_included=q["agents_included"], features=q["features"], amount_ht=q["vat"]["ht"],
        amount_vat=q["vat"]["vat"], vat_rate_bp=q["vat"]["rate_bp"], snapshot=snapshot)
    db.add(p)
    # Éléments du catalogue désormais « utilisés » : archivables, plus supprimables
    db.query(InvFormula).filter(InvFormula.id == q["formula"]["id"]).update({InvFormula.used: True})
    if q["pack"]["id"]:
        db.query(InvPack).filter(InvPack.id == q["pack"]["id"]).update({InvPack.used: True})
    if q["options"]:
        db.query(InvOption).filter(InvOption.id.in_([o["option_id"] for o in q["options"]])).update(
            {InvOption.used: True}, synchronize_session=False)
    if d and d["code_id"]:
        db.query(InvDiscountCode).filter(InvDiscountCode.id == d["code_id"]).update({InvDiscountCode.used: True})
    db.commit()
    db.refresh(p)
    parts = ["event %d : %s %d pers." % (ev.id, q["formula"]["name"], q["pack"]["size"])]
    if q["pack"]["custom"]:
        parts.append("sur mesure (%s)" % q["pack"]["custom_reason"][:80])
    if q["options"]:
        parts.append("options " + ", ".join("%s×%d" % (o["name"][:30], o["qty"]) for o in q["options"]))
    if d:
        parts.append("remise %s -%d FCFA%s" % (d["code"] or "manuelle", d["amount"],
                                                " (%s)" % d["reason"][:80] if d["reason"] else ""))
    if q["offered"]:
        parts.append("OFFERT : %s" % q["offered_reason"][:100])
    else:
        parts.append("total %d FCFA, %s réf. %s (reçu %d)" % (q["amount_due"], channel, ref[:40], received))
    log_security_event(db, "invitation_pack_offered" if q["offered"] else "invitation_pack_activated",
                       request, user.id, " · ".join(parts)[:1000])
    return p
