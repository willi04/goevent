"""
api/invitation_catalog.py — Catalogue des packs Invitations (lot 2.2).

  GET  /admin/invitations/catalog                 lecture : admin + commercial
  POST/PATCH/archive/restore/DELETE               formules, packs, options, codes : admin seul
  GET/PUT /admin/invitations/catalog/settings     TVA, plafond de remise manuelle : admin seul
  GET  /admin/invitations/catalog/history         historique (qui, quand, ancien → nouveau)
  POST /admin/invitations/events/{id}/packs/quote devis : admin + commercial (calcul de l'activation)
  GET  /invitations/catalog                       public (lot 3) : visibles, actifs, dans leurs dates

Suppression refusée (409) pour un élément déjà utilisé : on l'archive.
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import invitation_catalog as cat
from core import invitations as inv
from core.database import get_db
from core.limiter import limiter
from core.permissions import get_current_admin, is_admin, require_staff_function
from core.security import log_security_event
from models.invitation import PrivateEventSettings
from models.invitation_catalog import (
    InvCatalogLog, InvDiscountCode, InvFormula, InvOption, InvPack, inv_option_formulas,
)
from models.user import User
from schemas.invitation_catalog import (
    CatalogPackIn, CatalogPackPatch, CatalogSettingsIn, DiscountIn, DiscountPatch, FormulaIn, FormulaPatch,
    OptionIn, OptionPatch, QuoteIn,
)

router = APIRouter(tags=["invitations-catalogue"])
reader = require_staff_function("commercial")    # admin + commercial
_CODE_RE = re.compile(r"^[A-Z0-9_-]{3,40}$")


def _commit_log(db: Session, request: Request, user: User, entity: str, obj_id: Optional[int], action: str,
                label: str, changes: dict):
    if action == "update" and not changes:
        db.commit()
        return
    cat.log_change(db, entity, obj_id, action, label, changes, user.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Conflit : un élément identique et actif existe déjà.")
    log_security_event(db, "invitation_catalog_%s" % action, request, user.id,
                       ("%s %s « %s » " % (entity, obj_id, label[:60]) + cat._changes_txt(changes))[:1000])


def _dates(v):
    return inv.naive_utc(v) if v else None


def _check_period(start, end):
    if start and end and end < start:
        raise HTTPException(400, "La date de fin doit être après la date de début.")


def _get(db: Session, model, obj_id: int, lock: bool = False):
    q = db.query(model).filter(model.id == obj_id)
    obj = (q.with_for_update() if lock else q).first()
    if not obj:
        raise HTTPException(404, "Élément introuvable")
    return obj


def _archive(db, request, user, model, entity, obj_id, label_fn, check_restore=None):
    obj = _get(db, model, obj_id, lock=True)
    if obj.status == "archived":
        return obj
    obj.status = "archived"
    _commit_log(db, request, user, entity, obj.id, "archive", label_fn(obj), {"status": ["active", "archived"]})
    return obj


def _restore(db, request, user, model, entity, obj_id, label_fn, check=None):
    obj = _get(db, model, obj_id, lock=True)
    if obj.status == "active":
        return obj
    if check:
        check(obj)
    obj.status = "active"
    _commit_log(db, request, user, entity, obj.id, "restore", label_fn(obj), {"status": ["archived", "active"]})
    return obj


# ════════════════════════════════════════════════════════════
# Lecture / réglages / historique
# ════════════════════════════════════════════════════════════
@router.get("/admin/invitations/catalog")
def admin_catalog(db: Session = Depends(get_db), user: User = Depends(reader)):
    out = cat.full_catalog(db, include_archived=True)
    admin = is_admin(user)
    if not admin:
        out["discounts"] = [{k: d[k] for k in ("id", "code", "kind", "value", "status", "valid_from", "valid_until",
                                              "formula_ids", "pack_ids", "max_uses", "uses")} for d in out["discounts"]]
    out["can_edit"] = admin
    return out


@router.put("/admin/invitations/catalog/settings")
@limiter.limit("20/minute")
def admin_catalog_settings(request: Request, data: CatalogSettingsIn, db: Session = Depends(get_db),
                           user: User = Depends(get_current_admin)):
    payload = data.model_dump(exclude_none=True)
    if "vat_rate" in payload:
        payload["vat_rate_bp"] = int(round(payload.pop("vat_rate") * 100))
    return cat.save_settings(db, payload, user.id, request)


@router.get("/admin/invitations/catalog/history")
def admin_catalog_history(entity: Optional[str] = Query(None, max_length=20), entity_id: Optional[int] = None,
                          limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                          user: User = Depends(reader)):
    q = db.query(InvCatalogLog, User.full_name).outerjoin(User, User.id == InvCatalogLog.user_id)
    if entity:
        q = q.filter(InvCatalogLog.entity == entity)
    if entity_id:
        q = q.filter(InvCatalogLog.entity_id == entity_id)
    rows = q.order_by(InvCatalogLog.at.desc(), InvCatalogLog.id.desc()).limit(limit).all()
    return {"history": [{"id": h.id, "entity": h.entity, "entity_id": h.entity_id, "action": h.action,
                         "label": h.label, "changes": h.changes, "at": h.at.isoformat(),
                         "user": name or ("Reprise automatique" if h.action == "seed" else None)} for h, name in rows]}


# ════════════════════════════════════════════════════════════
# Formules
# ════════════════════════════════════════════════════════════
def _clean_included(items) -> list:
    out = [" ".join(str(i).split())[:120] for i in (items or []) if str(i).strip()]
    return out[:12]


def _formula_rules(f: InvFormula):
    if f.onsite_agents and not f.agents_step:
        raise HTTPException(400, "Agents sur place : indiquez le nombre d'invités par agent (ex. 300).")


@router.post("/admin/invitations/catalog/formulas")
@limiter.limit("60/minute")
def create_formula(request: Request, data: FormulaIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_admin)):
    if db.query(InvFormula.id).filter(InvFormula.code == data.code).first():
        raise HTTPException(409, "Ce code de formule existe déjà.")
    f = InvFormula(code=data.code, name=data.name.strip(), description=(data.description or "").strip() or None,
                   included=_clean_included(data.included), badge=(data.badge or "").strip() or None,
                   public=True if data.public is None else data.public, onsite_agents=bool(data.onsite_agents),
                   track_link=bool(data.track_link), team_sends=bool(data.team_sends), agents_step=data.agents_step,
                   sort_order=data.sort_order or 0)
    _formula_rules(f)
    db.add(f)
    db.flush()
    _commit_log(db, request, user, "formula", f.id, "create", f.name,
                {k: [None, cat._plain(v)] for k, v in cat.formula_fields(f).items()})
    return cat.formula_out(f)


@router.patch("/admin/invitations/catalog/formulas/{formula_id}")
@limiter.limit("60/minute")
def update_formula(request: Request, formula_id: int, data: FormulaPatch, db: Session = Depends(get_db),
                   user: User = Depends(get_current_admin)):
    f = _get(db, InvFormula, formula_id, lock=True)
    before = cat.formula_fields(f)
    fs = data.model_fields_set
    for k in ("public", "onsite_agents", "track_link", "team_sends"):
        if k in fs and getattr(data, k) is not None:
            setattr(f, k, getattr(data, k))
    if "name" in fs and data.name:
        f.name = data.name.strip()
    if "description" in fs:
        f.description = (data.description or "").strip() or None
    if "badge" in fs:
        f.badge = (data.badge or "").strip() or None
    if "included" in fs:
        f.included = _clean_included(data.included)
    if "agents_step" in fs:
        f.agents_step = data.agents_step
    if "sort_order" in fs and data.sort_order is not None:
        f.sort_order = data.sort_order
    _formula_rules(f)
    _commit_log(db, request, user, "formula", f.id, "update", f.name, cat.diff(before, cat.formula_fields(f)))
    return cat.formula_out(f)


@router.post("/admin/invitations/catalog/formulas/{formula_id}/archive")
@limiter.limit("60/minute")
def archive_formula(request: Request, formula_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_admin)):
    return cat.formula_out(_archive(db, request, user, InvFormula, "formula", formula_id, lambda o: o.name))


@router.post("/admin/invitations/catalog/formulas/{formula_id}/restore")
@limiter.limit("60/minute")
def restore_formula(request: Request, formula_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_admin)):
    return cat.formula_out(_restore(db, request, user, InvFormula, "formula", formula_id, lambda o: o.name))


@router.delete("/admin/invitations/catalog/formulas/{formula_id}")
@limiter.limit("30/minute")
def delete_formula(request: Request, formula_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_admin)):
    f = _get(db, InvFormula, formula_id, lock=True)
    in_use = (f.used or db.query(InvPack.id).filter(InvPack.formula_id == f.id).first()
              or db.query(PrivateEventSettings.event_id).filter(PrivateEventSettings.service_level == f.code).first())
    if in_use:
        raise HTTPException(409, "Formule déjà utilisée (packs, événements ou commandes) : archivez-la.")
    db.execute(inv_option_formulas.delete().where(inv_option_formulas.c.formula_id == f.id))
    name = f.name
    db.delete(f)
    _commit_log(db, request, user, "formula", formula_id, "delete", name, {})
    return {"deleted": formula_id}


# ════════════════════════════════════════════════════════════
# Packs
# ════════════════════════════════════════════════════════════
def _pack_label(db: Session, p: InvPack) -> str:
    f = db.query(InvFormula).filter(InvFormula.id == p.formula_id).first()
    return "%s %d pers.%s" % (f.name if f else "?", p.size, " « %s »" % p.name if p.name else "")


def _pack_rules(db: Session, p: InvPack):
    if p.on_quote:
        p.price_fcfa = None
    elif p.price_fcfa is None:
        raise HTTPException(400, "Indiquez un prix (ou cochez « sur devis »).")
    _check_period(p.valid_from, p.valid_until)
    if p.status == "active" and db.query(InvPack.id).filter(
            InvPack.formula_id == p.formula_id, InvPack.size == p.size, InvPack.status == "active",
            InvPack.id != (p.id or 0)).first():
        raise HTTPException(409, "Un pack actif de %d personnes existe déjà dans cette formule." % p.size)


@router.post("/admin/invitations/catalog/packs")
@limiter.limit("120/minute")
def create_pack(request: Request, data: CatalogPackIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_admin)):
    f = _get(db, InvFormula, data.formula_id)
    if f.status != "active":
        raise HTTPException(400, "Formule archivée : restaurez-la d'abord.")
    p = InvPack(formula_id=f.id, size=data.size, price_fcfa=data.price_fcfa, on_quote=bool(data.on_quote),
                name=(data.name or "").strip() or None, public=True if data.public is None else data.public,
                sort_order=data.sort_order if data.sort_order is not None else data.size,
                valid_from=_dates(data.valid_from), valid_until=_dates(data.valid_until), status="active")
    _pack_rules(db, p)
    db.add(p)
    db.flush()
    _commit_log(db, request, user, "pack", p.id, "create", _pack_label(db, p),
                {k: [None, cat._plain(v)] for k, v in cat.pack_fields(p).items()})
    return cat.pack_out(p)


@router.patch("/admin/invitations/catalog/packs/{pack_id}")
@limiter.limit("120/minute")
def update_pack(request: Request, pack_id: int, data: CatalogPackPatch, db: Session = Depends(get_db),
                user: User = Depends(get_current_admin)):
    p = _get(db, InvPack, pack_id, lock=True)
    before = cat.pack_fields(p)
    fs = data.model_fields_set
    if "size" in fs and data.size:
        p.size = data.size
    if "price_fcfa" in fs:
        p.price_fcfa = data.price_fcfa
    if "on_quote" in fs and data.on_quote is not None:
        p.on_quote = data.on_quote
    if "name" in fs:
        p.name = (data.name or "").strip() or None
    if "public" in fs and data.public is not None:
        p.public = data.public
    if "sort_order" in fs and data.sort_order is not None:
        p.sort_order = data.sort_order
    if "valid_from" in fs:
        p.valid_from = _dates(data.valid_from)
    if "valid_until" in fs:
        p.valid_until = _dates(data.valid_until)
    _pack_rules(db, p)
    _commit_log(db, request, user, "pack", p.id, "update", _pack_label(db, p), cat.diff(before, cat.pack_fields(p)))
    return cat.pack_out(p)


@router.post("/admin/invitations/catalog/packs/{pack_id}/archive")
@limiter.limit("120/minute")
def archive_pack(request: Request, pack_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    return cat.pack_out(_archive(db, request, user, InvPack, "pack", pack_id, lambda o: _pack_label(db, o)))


@router.post("/admin/invitations/catalog/packs/{pack_id}/restore")
@limiter.limit("120/minute")
def restore_pack(request: Request, pack_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    def check(p):
        p.status = "active"
        _pack_rules(db, p)
    return cat.pack_out(_restore(db, request, user, InvPack, "pack", pack_id, lambda o: _pack_label(db, o), check))


@router.delete("/admin/invitations/catalog/packs/{pack_id}")
@limiter.limit("60/minute")
def delete_pack(request: Request, pack_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    p = _get(db, InvPack, pack_id, lock=True)
    if p.used:
        raise HTTPException(409, "Pack déjà vendu : archivez-le (il reste dans l'historique des commandes).")
    label = _pack_label(db, p)
    db.delete(p)
    _commit_log(db, request, user, "pack", pack_id, "delete", label, {})
    return {"deleted": pack_id}


# ════════════════════════════════════════════════════════════
# Options
# ════════════════════════════════════════════════════════════
def _option_formula_ids(db: Session, oid: int) -> list:
    return sorted(fid for (fid,) in db.query(inv_option_formulas.c.formula_id)
                  .filter(inv_option_formulas.c.option_id == oid).all())


def _set_option_formulas(db: Session, oid: int, ids: list):
    ids = sorted(set(ids))
    if not ids:
        raise HTTPException(400, "Choisissez au moins une formule pour cette option.")
    found = {fid for (fid,) in db.query(InvFormula.id).filter(InvFormula.id.in_(ids)).all()}
    if found != set(ids):
        raise HTTPException(400, "Formule inconnue dans la sélection.")
    db.execute(inv_option_formulas.delete().where(inv_option_formulas.c.option_id == oid))
    for fid in ids:
        db.execute(inv_option_formulas.insert().values(option_id=oid, formula_id=fid))


def _option_rules(o: InvOption):
    if o.unit == "flat":
        o.max_qty = 1


@router.post("/admin/invitations/catalog/options")
@limiter.limit("60/minute")
def create_option(request: Request, data: OptionIn, db: Session = Depends(get_db),
                  user: User = Depends(get_current_admin)):
    o = InvOption(name=data.name.strip(), description=(data.description or "").strip() or None,
                  price_fcfa=data.price_fcfa, unit=data.unit, unit_label=(data.unit_label or "").strip() or None,
                  max_qty=data.max_qty or 1, agents_per_unit=data.agents_per_unit or 0,
                  public=True if data.public is None else data.public, sort_order=data.sort_order or 0)
    _option_rules(o)
    db.add(o)
    db.flush()
    _set_option_formulas(db, o.id, data.formula_ids)
    _commit_log(db, request, user, "option", o.id, "create", o.name,
                {k: [None, v] for k, v in cat.option_fields(o, data.formula_ids).items()})
    return cat.option_out(o, _option_formula_ids(db, o.id))


@router.patch("/admin/invitations/catalog/options/{option_id}")
@limiter.limit("60/minute")
def update_option(request: Request, option_id: int, data: OptionPatch, db: Session = Depends(get_db),
                  user: User = Depends(get_current_admin)):
    o = _get(db, InvOption, option_id, lock=True)
    before = cat.option_fields(o, _option_formula_ids(db, o.id))
    fs = data.model_fields_set
    for k in ("price_fcfa", "unit", "max_qty", "agents_per_unit", "public", "sort_order"):
        if k in fs and getattr(data, k) is not None:
            setattr(o, k, getattr(data, k))
    if "name" in fs and data.name:
        o.name = data.name.strip()
    if "description" in fs:
        o.description = (data.description or "").strip() or None
    if "unit_label" in fs:
        o.unit_label = (data.unit_label or "").strip() or None
    if "formula_ids" in fs and data.formula_ids is not None:
        _set_option_formulas(db, o.id, data.formula_ids)
    _option_rules(o)
    db.flush()
    after = cat.option_fields(o, _option_formula_ids(db, o.id))
    _commit_log(db, request, user, "option", o.id, "update", o.name, cat.diff(before, after))
    return cat.option_out(o, after["formula_ids"])


@router.post("/admin/invitations/catalog/options/{option_id}/archive")
@limiter.limit("60/minute")
def archive_option(request: Request, option_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    o = _archive(db, request, user, InvOption, "option", option_id, lambda x: x.name)
    return cat.option_out(o, _option_formula_ids(db, o.id))


@router.post("/admin/invitations/catalog/options/{option_id}/restore")
@limiter.limit("60/minute")
def restore_option(request: Request, option_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    o = _restore(db, request, user, InvOption, "option", option_id, lambda x: x.name)
    return cat.option_out(o, _option_formula_ids(db, o.id))


@router.delete("/admin/invitations/catalog/options/{option_id}")
@limiter.limit("30/minute")
def delete_option(request: Request, option_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    o = _get(db, InvOption, option_id, lock=True)
    if o.used:
        raise HTTPException(409, "Option déjà vendue : archivez-la.")
    name = o.name
    db.execute(inv_option_formulas.delete().where(inv_option_formulas.c.option_id == o.id))
    db.delete(o)
    _commit_log(db, request, user, "option", option_id, "delete", name, {})
    return {"deleted": option_id}


# ════════════════════════════════════════════════════════════
# Codes de remise (sur le PACK uniquement)
# ════════════════════════════════════════════════════════════
def _discount_rules(db: Session, d: InvDiscountCode):
    if d.kind == "pct" and not 1 <= d.value <= 100:
        raise HTTPException(400, "Pourcentage : entre 1 et 100.")
    _check_period(d.valid_from, d.valid_until)
    if d.formula_ids:
        n = db.query(InvFormula.id).filter(InvFormula.id.in_(d.formula_ids)).count()
        if n != len(set(d.formula_ids)):
            raise HTTPException(400, "Formule inconnue dans la portée du code.")
    if d.pack_ids:
        n = db.query(InvPack.id).filter(InvPack.id.in_(d.pack_ids)).count()
        if n != len(set(d.pack_ids)):
            raise HTTPException(400, "Pack inconnu dans la portée du code.")


@router.post("/admin/invitations/catalog/discounts")
@limiter.limit("60/minute")
def create_discount(request: Request, data: DiscountIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_admin)):
    code = data.code.strip().upper()
    if not _CODE_RE.match(code):
        raise HTTPException(400, "Code : 3 à 40 caractères, lettres, chiffres, tiret ou souligné.")
    if db.query(InvDiscountCode.id).filter(InvDiscountCode.code == code).first():
        raise HTTPException(409, "Ce code existe déjà.")
    d = InvDiscountCode(code=code, kind=data.kind, value=data.value, max_uses=data.max_uses,
                        valid_from=_dates(data.valid_from), valid_until=_dates(data.valid_until),
                        formula_ids=sorted(set(data.formula_ids or [])), pack_ids=sorted(set(data.pack_ids or [])),
                        note=(data.note or "").strip() or None)
    _discount_rules(db, d)
    db.add(d)
    db.flush()
    _commit_log(db, request, user, "discount", d.id, "create", d.code,
                {k: [None, cat._plain(v)] for k, v in cat.discount_fields(d).items()})
    return cat.discount_out(d, 0)


@router.patch("/admin/invitations/catalog/discounts/{discount_id}")
@limiter.limit("60/minute")
def update_discount(request: Request, discount_id: int, data: DiscountPatch, db: Session = Depends(get_db),
                    user: User = Depends(get_current_admin)):
    d = _get(db, InvDiscountCode, discount_id, lock=True)
    before = cat.discount_fields(d)
    fs = data.model_fields_set
    if "kind" in fs and data.kind:
        d.kind = data.kind
    if "value" in fs and data.value:
        d.value = data.value
    if "max_uses" in fs:
        d.max_uses = data.max_uses
    if "valid_from" in fs:
        d.valid_from = _dates(data.valid_from)
    if "valid_until" in fs:
        d.valid_until = _dates(data.valid_until)
    if "formula_ids" in fs:
        d.formula_ids = sorted(set(data.formula_ids or []))
    if "pack_ids" in fs:
        d.pack_ids = sorted(set(data.pack_ids or []))
    if "note" in fs:
        d.note = (data.note or "").strip() or None
    _discount_rules(db, d)
    _commit_log(db, request, user, "discount", d.id, "update", d.code, cat.diff(before, cat.discount_fields(d)))
    return cat.discount_out(d, cat.code_uses(db, d.id))


@router.post("/admin/invitations/catalog/discounts/{discount_id}/archive")
@limiter.limit("60/minute")
def archive_discount(request: Request, discount_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    d = _archive(db, request, user, InvDiscountCode, "discount", discount_id, lambda x: x.code)
    return cat.discount_out(d, cat.code_uses(db, d.id))


@router.post("/admin/invitations/catalog/discounts/{discount_id}/restore")
@limiter.limit("60/minute")
def restore_discount(request: Request, discount_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    d = _restore(db, request, user, InvDiscountCode, "discount", discount_id, lambda x: x.code)
    return cat.discount_out(d, cat.code_uses(db, d.id))


@router.delete("/admin/invitations/catalog/discounts/{discount_id}")
@limiter.limit("30/minute")
def delete_discount(request: Request, discount_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    d = _get(db, InvDiscountCode, discount_id, lock=True)
    if d.used:
        raise HTTPException(409, "Code déjà utilisé : archivez-le.")
    code = d.code
    db.delete(d)
    _commit_log(db, request, user, "discount", discount_id, "delete", code, {})
    return {"deleted": discount_id}


# ════════════════════════════════════════════════════════════
# Devis (même calcul que l'activation) + catalogue public
# ════════════════════════════════════════════════════════════
@router.post("/admin/invitations/events/{event_id}/packs/quote")
@limiter.limit("120/minute")
def admin_quote(request: Request, event_id: int, data: QuoteIn, db: Session = Depends(get_db),
                user: User = Depends(reader)):
    ev = inv.get_private_event(db, event_id)
    return cat.quote(db, ev, is_admin(user), data.model_dump())


@router.get("/invitations/catalog")
@limiter.limit("60/minute")
def public_catalog(request: Request, db: Session = Depends(get_db)):
    return JSONResponse(cat.public_catalog(db), headers={"Cache-Control": "public, max-age=60"})
