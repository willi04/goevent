"""
api/zones.py — Emplacements (blocs : affiches, bannières, annonces, cartes) et tarifs.

Public (web/cms.js) :
  GET  /zones?keys=a,b        blocs en diffusion + réglages des zones demandées
  GET  /zones/media/{id}      image d'un bloc (cache long, URL versionnée)
  POST /zones/events          compteurs d'affichages / clics (beacon, texte JSON)
  GET  /pricing               tarifs en vigueur (source unique, cf. core/pricing.py)

Back-office :
  blocs et réglages des zones → admin + CM (même droit que « Contenu du site »)
  tarifs                      → admin
"""
import base64
import json
import zlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.cms import _REL_URL, _cached, _validate_image, content_editor
from core.database import get_db
from core.limiter import limiter
from core.permissions import get_current_admin
from core.pricing import (
    DEFAULT_PRICING, LABELS, get_pricing, invalidate_cache, public_pricing, validate_update,
)
from core.security import log_security_event
from core.zones import CARD_ICONS, KINDS, THEMES, ZONE_BY_KEY, ZONE_PAGES, ZONES, zone_settings
from models.site_content import SiteSetting, ZoneBlock
from models.user import User
from schemas.site_content import PricingIn, ZoneBlockIn, ZoneBlockPatch, ZoneOrder, ZoneSettingsIn

router = APIRouter(tags=["zones"])

# ── helpers ───────────────────────────────────────────────────────────
def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _setting(db: Session, key: str) -> dict:
    row = db.query(SiteSetting).filter(SiteSetting.key == key).first()
    if not row:
        return {}
    try:
        return json.loads(row.value)
    except ValueError:
        return {}


def _save_setting(db: Session, key: str, value: dict, user_id: int):
    row = db.query(SiteSetting).filter(SiteSetting.key == key).first()
    raw = json.dumps(value, ensure_ascii=False)
    if row:
        row.value, row.updated_by, row.updated_at = raw, user_id, datetime.utcnow()
    else:
        db.add(SiteSetting(key=key, value=raw, updated_by=user_id))


def _clean_text(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = "".join(ch for ch in v if ch == "\n" or ord(ch) >= 32).strip()
    return v or None


def _clean_link(v: Optional[str]) -> Optional[str]:
    v = _clean_text(v)
    if not v:
        return None
    low = v.lower()
    if not (low.startswith(("https://", "http://", "mailto:", "tel:")) or _REL_URL.match(v)):
        raise HTTPException(400, "Lien invalide (https://…, mailto:, tel: ou page du site, ex. partenaire.html).")
    return v


def _clean_items(items) -> Optional[str]:
    if items is None:
        return None
    out = []
    for it in items:
        icon = it.icon if it.icon in CARD_ICONS else "star"
        out.append({"icon": icon, "title": _clean_text(it.title) or "", "text": _clean_text(it.text) or ""})
    return json.dumps(out, ensure_ascii=False) if out else None


def _status(b: ZoneBlock, now: datetime) -> str:
    if not b.is_active:
        return "inactive"
    if b.starts_at and b.starts_at > now:
        return "scheduled"
    if b.ends_at and b.ends_at <= now:
        return "ended"
    return "live"


def _image_url(b: ZoneBlock) -> Optional[str]:
    if not b.image:
        return None
    if b.image.startswith("data:"):
        return "/zones/media/%d?v=%d" % (b.id, int(b.updated_at.timestamp()))
    return b.image


def _public_block(b: ZoneBlock) -> dict:
    return {"id": b.id, "kind": b.kind, "title": b.title, "body": b.body, "image": _image_url(b),
            "link_url": b.link_url, "link_label": b.link_label, "sponsor": b.sponsor, "theme": b.theme,
            "items": json.loads(b.items) if b.items else [],
            # version du CONTENU (pas updated_at, que les compteurs d'affichage modifient) :
            # une annonce modifiée réapparaît même si le visiteur l'avait fermée
            "rev": zlib.crc32(json.dumps([b.kind, b.title, b.body, b.link_url, b.link_label, b.theme]).encode())}


def _admin_block(b: ZoneBlock, now: datetime) -> dict:
    d = _public_block(b)
    d.update({"zone": b.zone, "position": b.position, "is_active": b.is_active,
              "starts_at": b.starts_at.isoformat() if b.starts_at else None,
              "ends_at": b.ends_at.isoformat() if b.ends_at else None,
              "impressions": b.impressions, "clicks": b.clicks, "status": _status(b, now),
              "updated_at": b.updated_at.isoformat() if b.updated_at else None})
    return d


def _check_block(zone: dict, kind: str, image: Optional[str], items, theme: Optional[str],
                 starts: Optional[datetime], ends: Optional[datetime], title: Optional[str]):
    if kind not in zone["kinds"]:
        raise HTTPException(400, "Type « %s » non autorisé dans cet emplacement (%s)."
                            % (KINDS.get(kind, kind), ", ".join(KINDS[k] for k in zone["kinds"])))
    if kind == "image" and not image:
        raise HTTPException(400, "Une affiche nécessite une image.")
    if kind == "cards" and not items:
        raise HTTPException(400, "Ajoutez au moins une carte.")
    if kind in ("announcement", "banner") and not title and not image:
        raise HTTPException(400, "Indiquez au moins un titre.")
    if theme is not None and theme not in THEMES:
        raise HTTPException(400, "Couleur invalide.")
    if starts and ends and ends <= starts:
        raise HTTPException(400, "La date de fin doit être après la date de début.")


# ════════════════════════════════════════════════════════════
# PUBLIC
# ════════════════════════════════════════════════════════════

@router.get("/zones")
@limiter.limit("240/minute")
def public_zones(request: Request, keys: str = Query(..., max_length=500), db: Session = Depends(get_db)):
    wanted = [k for k in dict.fromkeys(x.strip() for x in keys.split(",")) if k in ZONE_BY_KEY][:12]
    now = datetime.utcnow()
    stored = _setting(db, "zones")
    blocks = []
    if wanted:
        blocks = (db.query(ZoneBlock)
                  .filter(ZoneBlock.zone.in_(wanted), ZoneBlock.is_active == True,  # noqa: E712
                          or_(ZoneBlock.starts_at.is_(None), ZoneBlock.starts_at <= now),
                          or_(ZoneBlock.ends_at.is_(None), ZoneBlock.ends_at > now))
                  .order_by(ZoneBlock.position, ZoneBlock.id).all())
    zones = {}
    for key in wanted:
        z = ZONE_BY_KEY[key]
        s = zone_settings(stored, z)
        zones[key] = {"mode": z["mode"], "empty": s["empty"], "every": s["every"], "label": z["label"],
                      "blocks": [_public_block(b) for b in blocks if b.zone == key]}
    return _cached({"zones": zones})


@router.get("/zones/media/{block_id}")
@limiter.limit("240/minute")
def public_zone_media(request: Request, block_id: int, db: Session = Depends(get_db)):
    b = db.query(ZoneBlock).filter(ZoneBlock.id == block_id).first()
    if not b or not b.image or not b.image.startswith("data:image/"):
        raise HTTPException(404, "Image introuvable")
    header, b64 = b.image.split(",", 1)
    return Response(base64.b64decode(b64), media_type=header[5:].split(";", 1)[0],
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/zones/events")
@limiter.limit("60/minute")
async def public_zone_events(request: Request, db: Session = Depends(get_db)):
    """Beacon {"i": [ids affichés], "c": [ids cliqués]} envoyé en text/plain (pas de preflight CORS)."""
    try:
        data = json.loads((await request.body())[:2000] or b"{}")
    except ValueError:
        return {"ok": False}
    for field, col in (("i", ZoneBlock.impressions), ("c", ZoneBlock.clicks)):
        ids = [i for i in dict.fromkeys(data.get(field) or []) if isinstance(i, int)][:20]
        if ids:
            db.query(ZoneBlock).filter(ZoneBlock.id.in_(ids)).update({col: col + 1}, synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.get("/pricing")
@limiter.limit("240/minute")
def public_pricing_route(request: Request, db: Session = Depends(get_db)):
    return _cached(public_pricing(db))


# ════════════════════════════════════════════════════════════
# BACK-OFFICE — emplacements
# ════════════════════════════════════════════════════════════

@router.get("/admin/zones")
def admin_zones(db: Session = Depends(get_db), user: User = Depends(content_editor)):
    now = datetime.utcnow()
    stored = _setting(db, "zones")
    blocks = db.query(ZoneBlock).order_by(ZoneBlock.position, ZoneBlock.id).all()
    out = []
    for z in ZONES:
        s = zone_settings(stored, z)
        out.append({**z, **s, "blocks": [_admin_block(b, now) for b in blocks if b.zone == z["key"]]})
    return {"pages": ZONE_PAGES, "kinds": KINDS, "themes": list(THEMES), "icons": list(CARD_ICONS), "zones": out}


@router.post("/admin/zones/{zone_key}/blocks")
@limiter.limit("60/minute")
def admin_create_block(request: Request, zone_key: str, data: ZoneBlockIn, db: Session = Depends(get_db),
                       user: User = Depends(content_editor)):
    zone = ZONE_BY_KEY.get(zone_key)
    if not zone:
        raise HTTPException(404, "Emplacement inconnu")
    image = _validate_image(data.image.strip()) if data.image and data.image.strip() else None
    starts, ends = _naive_utc(data.starts_at), _naive_utc(data.ends_at)
    _check_block(zone, data.kind, image, data.items, data.theme, starts, ends, _clean_text(data.title))
    pos = (db.query(ZoneBlock.position).filter(ZoneBlock.zone == zone_key)
           .order_by(ZoneBlock.position.desc()).limit(1).scalar() or 0) + 1
    b = ZoneBlock(zone=zone_key, kind=data.kind, title=_clean_text(data.title), body=_clean_text(data.body),
                  image=image, link_url=_clean_link(data.link_url), link_label=_clean_text(data.link_label),
                  sponsor=_clean_text(data.sponsor), theme=data.theme, items=_clean_items(data.items),
                  is_active=data.is_active, starts_at=starts, ends_at=ends, position=pos, updated_by=user.id)
    db.add(b)
    db.commit()
    db.refresh(b)
    log_security_event(db, "zone_block_created", request, user.id, "%s #%d (%s)" % (zone_key, b.id, b.kind))
    return _admin_block(b, datetime.utcnow())


@router.patch("/admin/zones/blocks/{block_id}")
@limiter.limit("60/minute")
def admin_update_block(request: Request, block_id: int, data: ZoneBlockPatch, db: Session = Depends(get_db),
                       user: User = Depends(content_editor)):
    b = db.query(ZoneBlock).filter(ZoneBlock.id == block_id).first()
    if not b:
        raise HTTPException(404, "Bloc introuvable")
    fields = data.model_fields_set
    if "image" in fields:
        b.image = _validate_image(data.image.strip()) if data.image and data.image.strip() else None
    for name in ("title", "body", "link_label", "sponsor"):
        if name in fields:
            setattr(b, name, _clean_text(getattr(data, name)))
    if "link_url" in fields:
        b.link_url = _clean_link(data.link_url)
    if "theme" in fields and data.theme is not None:
        b.theme = data.theme
    if "items" in fields:
        b.items = _clean_items(data.items)
    if "is_active" in fields and data.is_active is not None:
        b.is_active = data.is_active
    if data.clear_dates:
        b.starts_at = b.ends_at = None
    if "starts_at" in fields:
        b.starts_at = _naive_utc(data.starts_at)
    if "ends_at" in fields:
        b.ends_at = _naive_utc(data.ends_at)
    _check_block(ZONE_BY_KEY[b.zone], b.kind, b.image, json.loads(b.items) if b.items else None,
                 b.theme, b.starts_at, b.ends_at, b.title)
    b.updated_by = user.id
    b.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(b)
    log_security_event(db, "zone_block_updated", request, user.id, "%s #%d" % (b.zone, b.id))
    return _admin_block(b, datetime.utcnow())


@router.delete("/admin/zones/blocks/{block_id}")
@limiter.limit("60/minute")
def admin_delete_block(request: Request, block_id: int, db: Session = Depends(get_db),
                       user: User = Depends(content_editor)):
    b = db.query(ZoneBlock).filter(ZoneBlock.id == block_id).first()
    if not b:
        raise HTTPException(404, "Bloc introuvable")
    info = "%s #%d (%s, %d affichages, %d clics)" % (b.zone, b.id, b.title or b.kind, b.impressions, b.clicks)
    db.delete(b)
    db.commit()
    log_security_event(db, "zone_block_deleted", request, user.id, info[:300])
    return {"deleted": block_id}


@router.put("/admin/zones/{zone_key}/order")
@limiter.limit("60/minute")
def admin_order_blocks(request: Request, zone_key: str, data: ZoneOrder, db: Session = Depends(get_db),
                       user: User = Depends(content_editor)):
    if zone_key not in ZONE_BY_KEY:
        raise HTTPException(404, "Emplacement inconnu")
    blocks = {b.id: b for b in db.query(ZoneBlock).filter(ZoneBlock.zone == zone_key,
                                                           ZoneBlock.id.in_(data.ids)).all()}
    for pos, bid in enumerate(data.ids):
        if bid in blocks:
            blocks[bid].position = pos
    db.commit()
    return {"ok": True}


@router.put("/admin/zones/{zone_key}/settings")
@limiter.limit("60/minute")
def admin_zone_settings(request: Request, zone_key: str, data: ZoneSettingsIn, db: Session = Depends(get_db),
                        user: User = Depends(content_editor)):
    zone = ZONE_BY_KEY.get(zone_key)
    if not zone:
        raise HTTPException(404, "Emplacement inconnu")
    stored = _setting(db, "zones")
    s = dict(stored.get(zone_key) or {})
    s.pop("empty", None)  # « Si vide » n'est plus réglable (zone vide = masquée)
    if data.every is not None:
        if not zone.get("every"):
            raise HTTPException(400, "Réglage non disponible pour cet emplacement.")
        s["every"] = data.every
    stored[zone_key] = s
    _save_setting(db, "zones", stored, user.id)
    db.commit()
    log_security_event(db, "zone_settings_updated", request, user.id, "%s %s" % (zone_key, json.dumps(s)))
    return zone_settings(stored, zone)


# ════════════════════════════════════════════════════════════
# BACK-OFFICE — tarifs (admin)
# ════════════════════════════════════════════════════════════

@router.get("/admin/pricing")
def admin_pricing(db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    row = db.query(SiteSetting).filter(SiteSetting.key == "pricing").first()
    return {"pricing": public_pricing(db), "defaults": DEFAULT_PRICING, "labels": LABELS,
            "updated_at": row.updated_at.isoformat() if row else None}


@router.put("/admin/pricing")
@limiter.limit("20/minute")
def admin_update_pricing(request: Request, data: PricingIn, db: Session = Depends(get_db),
                         user: User = Depends(get_current_admin)):
    try:
        clean = validate_update(data.pricing)
    except ValueError as e:
        raise HTTPException(400, str(e))
    before = get_pricing(db, fresh=True)
    stored = _setting(db, "pricing")
    for group, values in clean.items():
        stored.setdefault(group, {}).update(values)
    _save_setting(db, "pricing", stored, user.id)
    db.commit()
    invalidate_cache()
    after = get_pricing(db, fresh=True)
    changes = []
    for group, values in after.items():
        for key, val in values.items():
            old = before[group][key]
            if old != val:
                o = old["amount"] if isinstance(old, dict) else old
                n = val["amount"] if isinstance(val, dict) else val
                changes.append("%s.%s: %s → %s" % (group, key, o, n))
    if changes:
        log_security_event(db, "pricing_update", request, user.id, "; ".join(changes)[:1000])
    return {"pricing": public_pricing(db), "changes": changes}
