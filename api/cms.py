"""
api/cms.py — Back-office de contenu du site.

Public (lu par web/cms.js) :
  GET /content?pages=index,global   surcharges de textes/images/liens
  GET /content/media/{key}          image uploadée (cache long, URL versionnée)
  GET /pages/{slug}                 page légale publiée (dernière révision)
  GET /faq                          FAQ du centre d'aide

Back-office :
  textes, images, liens, FAQ  → admin + staff CM  (require_staff_function("cm"))
  pages légales et champs « admin_only » (montants FCFA, en-têtes légaux) → admin

Sécurité : les textes sont appliqués côté navigateur en textContent (jamais
innerHTML) ; seuls les corps de pages légales et les réponses FAQ sont du HTML,
nettoyé ici par bleach (liste blanche). Images : PNG/JPEG/WebP uniquement
(SVG refusé : vecteur XSS), type vérifié sur les octets.
"""
import base64
import binascii
import re
from datetime import datetime
from typing import Optional

import bleach
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.cms_registry import get_registry, pages_summary
from core.database import get_db
from core.limiter import limiter
from core.permissions import get_current_admin, is_admin, require_staff_function
from core.security import log_security_event
from models.site_content import FaqItem, SiteContent, SitePageRevision
from models.user import User
from schemas.site_content import (
    ContentValuesUpdate, FaqImport, FaqItemCreate, FaqItemUpdate, FaqOrder, PageRevisionCreate,
)

router = APIRouter(tags=["cms"])

content_editor = require_staff_function("cm")  # admin + CM

LEGAL_TITLES = {
    "cgu": "Conditions Générales d'Utilisation",
    "confidentialite": "Politique de Confidentialité",
    "remboursements": "Politique de Remboursements",
}
FAQ_CATEGORIES = {
    "compte": "Mon compte", "achat": "Achat & paiement", "billet": "Mes billets",
    "orga": "Organisateurs", "autre": "Autres",
}

MAX_LEN = {"text": 300, "textarea": 3000, "url": 500, "email": 254, "phone": 20}
MAX_IMAGE_BYTES = 1_500_000
IMAGE_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}

PAGE_TAGS = ["h2", "h3", "h4", "p", "br", "strong", "b", "em", "i", "u", "a", "ul", "ol", "li",
             "blockquote", "table", "thead", "tbody", "tr", "th", "td", "div", "span", "hr"]
FAQ_TAGS = ["p", "br", "strong", "b", "em", "i", "u", "a", "ul", "ol", "li"]
HTML_ATTRS = {"a": ["href", "title", "target", "rel"], "th": ["colspan", "rowspan", "scope", "class"],
              "td": ["colspan", "rowspan", "class"], "*": ["class"],
              # ancres de section (ex. confidentialite.html#invitations, lien de la page Invitations)
              "h2": ["id", "class"], "h3": ["id", "class"]}
HTML_PROTOCOLS = ["http", "https", "mailto", "tel"]

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EMAIL = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[A-Za-z]{2,}$")
_REL_URL = re.compile(r"^[A-Za-z0-9_\-./#?=&%~+]+$")


def _clean_html(html: str, tags: list) -> str:
    return bleach.clean(html or "", tags=tags, attributes=HTML_ATTRS,
                        protocols=HTML_PROTOCOLS, strip=True, strip_comments=True).strip()


def _media_url(key: str, row: SiteContent) -> str:
    return "/content/media/%s?v=%d" % (key, int(row.updated_at.timestamp()))


def _public_value(key: str, field: dict, row: SiteContent) -> str:
    if field["kind"] == "image" and row.value.startswith("data:"):
        return _media_url(key, row)
    return row.value


def _validate(field: dict, raw: str) -> str:
    """Normalise et valide une valeur selon le type du champ. Lève 400."""
    kind, key = field["kind"], field["key"]
    value = _CTRL.sub("", raw).strip()
    if kind == "image":
        return _validate_image(value)
    limit = MAX_LEN.get(kind, 300)
    if len(value) > limit:
        raise HTTPException(400, "« %s » : %d caractères maximum." % (field["label"][:40], limit))
    if kind in ("text",):
        return " ".join(value.split())
    if kind == "email":
        if not _EMAIL.match(value):
            raise HTTPException(400, "Adresse email invalide.")
        return value
    if kind == "phone":
        digits = re.sub(r"[\s+\-().]", "", value)
        if not digits.isdigit() or not 8 <= len(digits) <= 15:
            raise HTTPException(400, "Numéro invalide : chiffres uniquement, format international (ex. 23672259398).")
        return digits
    if kind == "url":
        low = value.lower()
        if key.startswith("global.social."):
            if not low.startswith("https://"):
                raise HTTPException(400, "Lien de réseau social : doit commencer par https://")
        elif not (low.startswith(("https://", "http://", "mailto:", "tel:")) or _REL_URL.match(value)):
            raise HTTPException(400, "Lien invalide (https://…, mailto:, tel: ou page du site).")
        return value
    return value  # textarea : retours à la ligne conservés


def _validate_image(value: str) -> str:
    if value.lower().startswith("https://"):
        if len(value) > 1000:
            raise HTTPException(400, "URL d'image trop longue.")
        return value
    m = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", value)
    if not m:
        raise HTTPException(400, "Image refusée : PNG, JPEG ou WebP uniquement (ou URL https).")
    mime = m.group(1)
    try:
        data = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Image illisible.")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image trop lourde (1,5 Mo maximum après compression).")
    if not data.startswith(IMAGE_MAGIC[mime]) or (mime == "image/webp" and data[8:12] != b"WEBP"):
        raise HTTPException(400, "Le contenu du fichier ne correspond pas à une image %s." % mime)
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())


def _cached(payload, seconds=60):
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=%d" % seconds})


# ════════════════════════════════════════════════════════════
# PUBLIC
# ════════════════════════════════════════════════════════════

@router.get("/content")
@limiter.limit("240/minute")
def public_content(request: Request, pages: str = Query("global", max_length=200),
                   db: Session = Depends(get_db)):
    registry = get_registry()
    known = {f["page"] for f in registry.values()}
    wanted = [p for p in dict.fromkeys(x.strip() for x in pages.split(",")) if p in known][:6]
    if not wanted:
        return _cached({"values": {}, "v": 0})
    rows = db.query(SiteContent).filter(or_(*[SiteContent.key.like(p + ".%") for p in wanted])).all()
    values, latest = {}, 0
    if "global" in wanted:  # éléments communs : défauts + surcharges (footer, liens sociaux)
        values.update({k: f["default"] for k, f in registry.items() if f["page"] == "global"})
    for row in rows:
        field = registry.get(row.key)
        if not field:
            continue  # clé orpheline (élément retiré du HTML) : ignorée
        values[row.key] = _public_value(row.key, field, row)
        latest = max(latest, int(row.updated_at.timestamp()))
    return _cached({"values": values, "v": latest})


@router.get("/content/media/{key}")
@limiter.limit("240/minute")
def public_media(request: Request, key: str, db: Session = Depends(get_db)):
    row = db.query(SiteContent).filter(SiteContent.key == key).first()
    if not row or not row.value.startswith("data:image/"):
        raise HTTPException(404, "Image introuvable")
    header, b64 = row.value.split(",", 1)
    mime = header[5:].split(";", 1)[0]
    return Response(base64.b64decode(b64), media_type=mime,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/pages/{slug}")
@limiter.limit("120/minute")
def public_page(request: Request, slug: str, db: Session = Depends(get_db)):
    if slug not in LEGAL_TITLES:
        raise HTTPException(404, "Page inconnue")
    rev = (db.query(SitePageRevision).filter(SitePageRevision.slug == slug)
           .order_by(SitePageRevision.id.desc()).first())
    if not rev:
        raise HTTPException(404, "Aucune version publiée")  # cms.js garde le contenu statique
    version = db.query(func.count(SitePageRevision.id)).filter(SitePageRevision.slug == slug).scalar()
    return _cached({"slug": slug, "title": rev.title, "body_html": rev.body_html,
                    "updated_at": rev.created_at.isoformat(), "version": version})


@router.get("/faq")
@limiter.limit("120/minute")
def public_faq(request: Request, db: Session = Depends(get_db)):
    items = (db.query(FaqItem).filter(FaqItem.is_active == True)  # noqa: E712
             .order_by(FaqItem.position, FaqItem.id).all())
    return _cached({"categories": FAQ_CATEGORIES,
                    "items": [{"id": i.id, "category": i.category, "question": i.question,
                               "answer_html": i.answer_html} for i in items]})


# ════════════════════════════════════════════════════════════
# BACK-OFFICE — textes, images, liens
# ════════════════════════════════════════════════════════════

@router.get("/admin/cms/fields")
def admin_fields(db: Session = Depends(get_db), user: User = Depends(content_editor)):
    registry = get_registry()
    rows = {r.key: r for r in db.query(SiteContent).all()}
    fields = []
    for key, f in registry.items():
        row = rows.get(key)
        fields.append({**f, "overridden": row is not None,
                       "value": _public_value(key, f, row) if row else None,
                       "updated_at": row.updated_at.isoformat() if row else None})
    return {"pages": pages_summary(registry), "fields": fields, "can_admin": is_admin(user)}


@router.put("/admin/cms/fields")
@limiter.limit("60/minute")
def admin_update_fields(request: Request, data: ContentValuesUpdate, db: Session = Depends(get_db),
                        user: User = Depends(content_editor)):
    registry = get_registry()
    admin = is_admin(user)
    updated, reset = [], []
    for key, raw in data.values.items():
        field = registry.get(key)
        if not field:
            raise HTTPException(400, "Champ inconnu : %s" % key[:80])
        if field["admin_only"] and not admin:
            raise HTTPException(403, "« %s » : modification réservée à l'administrateur." % field["label"][:50])
        row = db.query(SiteContent).filter(SiteContent.key == key).first()
        value = None if raw is None else raw.strip()
        if value:
            value = _validate(field, value)
        if not value or value == field["default"]:
            if row:
                db.delete(row)
                reset.append(key)
            continue
        if row:
            row.value, row.updated_by, row.updated_at = value, user.id, datetime.utcnow()
        else:
            db.add(SiteContent(key=key, value=value, updated_by=user.id))
        updated.append(key)
    db.commit()
    if updated or reset:
        log_security_event(db, "cms_update", request, user.id,
                           ("modifiés: %s | réinitialisés: %s" % (", ".join(updated), ", ".join(reset)))[:1000])
    return {"updated": updated, "reset": reset}


# ════════════════════════════════════════════════════════════
# BACK-OFFICE — pages légales (admin)
# ════════════════════════════════════════════════════════════

@router.get("/admin/cms/pages")
def admin_pages(db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    out = []
    for slug, title in LEGAL_TITLES.items():
        rev = (db.query(SitePageRevision).filter(SitePageRevision.slug == slug)
               .order_by(SitePageRevision.id.desc()).first())
        out.append({"slug": slug, "default_title": title,
                    "current_title": rev.title if rev else None,
                    "updated_at": rev.created_at.isoformat() if rev else None})
    return out


@router.get("/admin/cms/pages/{slug}")
def admin_page_detail(slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    if slug not in LEGAL_TITLES:
        raise HTTPException(404, "Page inconnue")
    revs = (db.query(SitePageRevision).filter(SitePageRevision.slug == slug)
            .order_by(SitePageRevision.id.desc()).limit(100).all())
    authors = {u.id: (u.full_name or u.phone_number) for u in
               db.query(User).filter(User.id.in_({r.created_by for r in revs if r.created_by})).all()}
    current = revs[0] if revs else None
    return {
        "slug": slug, "default_title": LEGAL_TITLES[slug],
        "current": ({"id": current.id, "title": current.title, "body_html": current.body_html,
                     "created_at": current.created_at.isoformat()} if current else None),
        "revisions": [{"id": r.id, "title": r.title, "note": r.note, "created_at": r.created_at.isoformat(),
                       "author": authors.get(r.created_by, "—")} for r in revs],
    }


@router.get("/admin/cms/pages/{slug}/revisions/{rev_id}")
def admin_page_revision(slug: str, rev_id: int, db: Session = Depends(get_db),
                        user: User = Depends(get_current_admin)):
    rev = db.query(SitePageRevision).filter(SitePageRevision.id == rev_id,
                                            SitePageRevision.slug == slug).first()
    if not rev:
        raise HTTPException(404, "Version introuvable")
    return {"id": rev.id, "title": rev.title, "body_html": rev.body_html, "note": rev.note,
            "created_at": rev.created_at.isoformat()}


@router.post("/admin/cms/pages/{slug}")
@limiter.limit("20/minute")
def admin_publish_page(request: Request, slug: str, data: PageRevisionCreate, db: Session = Depends(get_db),
                       user: User = Depends(get_current_admin)):
    if slug not in LEGAL_TITLES:
        raise HTTPException(404, "Page inconnue")
    body = _clean_html(data.body_html, PAGE_TAGS)
    if len(bleach.clean(body, tags=[], strip=True).strip()) < 50:
        raise HTTPException(400, "Contenu trop court ou vide après nettoyage.")
    rev = SitePageRevision(slug=slug, title=" ".join(data.title.split()), body_html=body,
                           note=(data.note or "").strip() or None, created_by=user.id)
    db.add(rev)
    db.commit()
    db.refresh(rev)
    log_security_event(db, "cms_page_published", request, user.id,
                       "%s v#%d : %s" % (slug, rev.id, (rev.note or "")[:200]))
    return {"id": rev.id, "created_at": rev.created_at.isoformat()}


# ════════════════════════════════════════════════════════════
# BACK-OFFICE — FAQ (admin + CM)
# ════════════════════════════════════════════════════════════

def _faq_out(i: FaqItem) -> dict:
    return {"id": i.id, "category": i.category, "question": i.question, "answer_html": i.answer_html,
            "position": i.position, "is_active": i.is_active,
            "updated_at": i.updated_at.isoformat() if i.updated_at else None}


def _check_category(cat: Optional[str]):
    if cat is not None and cat not in FAQ_CATEGORIES:
        raise HTTPException(400, "Catégorie invalide (%s)." % ", ".join(FAQ_CATEGORIES))


def _clean_answer(html: str) -> str:
    cleaned = _clean_html(html, FAQ_TAGS)
    if not bleach.clean(cleaned, tags=[], strip=True).strip():
        raise HTTPException(400, "Réponse vide.")
    return cleaned


@router.get("/admin/cms/faq")
def admin_faq(db: Session = Depends(get_db), user: User = Depends(content_editor)):
    items = db.query(FaqItem).order_by(FaqItem.position, FaqItem.id).all()
    return {"categories": FAQ_CATEGORIES, "items": [_faq_out(i) for i in items]}


@router.post("/admin/cms/faq")
@limiter.limit("60/minute")
def admin_faq_create(request: Request, data: FaqItemCreate, db: Session = Depends(get_db),
                     user: User = Depends(content_editor)):
    _check_category(data.category)
    pos = (db.query(func.max(FaqItem.position)).scalar() or 0) + 1
    item = FaqItem(category=data.category, question=" ".join(data.question.split()),
                   answer_html=_clean_answer(data.answer_html), is_active=data.is_active,
                   position=pos, updated_by=user.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    log_security_event(db, "cms_faq_created", request, user.id, "FAQ #%d" % item.id)
    return _faq_out(item)


@router.patch("/admin/cms/faq/{item_id}")
@limiter.limit("60/minute")
def admin_faq_update(request: Request, item_id: int, data: FaqItemUpdate, db: Session = Depends(get_db),
                     user: User = Depends(content_editor)):
    item = db.query(FaqItem).filter(FaqItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Question introuvable")
    _check_category(data.category)
    if data.category is not None:
        item.category = data.category
    if data.question is not None:
        item.question = " ".join(data.question.split())
    if data.answer_html is not None:
        item.answer_html = _clean_answer(data.answer_html)
    if data.is_active is not None:
        item.is_active = data.is_active
    item.updated_by = user.id
    db.commit()
    db.refresh(item)
    log_security_event(db, "cms_faq_updated", request, user.id, "FAQ #%d" % item.id)
    return _faq_out(item)


@router.delete("/admin/cms/faq/{item_id}")
@limiter.limit("60/minute")
def admin_faq_delete(request: Request, item_id: int, db: Session = Depends(get_db),
                     user: User = Depends(content_editor)):
    item = db.query(FaqItem).filter(FaqItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Question introuvable")
    question = item.question
    db.delete(item)
    db.commit()
    log_security_event(db, "cms_faq_deleted", request, user.id, ("FAQ #%d : %s" % (item_id, question))[:300])
    return {"deleted": item_id}


@router.put("/admin/cms/faq/order")
@limiter.limit("60/minute")
def admin_faq_order(request: Request, data: FaqOrder, db: Session = Depends(get_db),
                    user: User = Depends(content_editor)):
    items = {i.id: i for i in db.query(FaqItem).filter(FaqItem.id.in_(data.ids)).all()}
    for pos, item_id in enumerate(data.ids):
        if item_id in items:
            items[item_id].position = pos
    db.commit()
    return {"ok": True}


@router.post("/admin/cms/faq/import")
@limiter.limit("10/minute")
def admin_faq_import(request: Request, data: FaqImport, db: Session = Depends(get_db),
                     user: User = Depends(content_editor)):
    existing = db.query(func.count(FaqItem.id)).scalar()
    if existing and not data.replace:
        raise HTTPException(409, "La FAQ contient déjà %d question(s)." % existing)
    for it in data.items:
        _check_category(it.category)
    if data.replace:
        db.query(FaqItem).delete()
    for pos, it in enumerate(data.items):
        db.add(FaqItem(category=it.category, question=" ".join(it.question.split()),
                       answer_html=_clean_answer(it.answer_html), is_active=it.is_active,
                       position=pos, updated_by=user.id))
    db.commit()
    log_security_event(db, "cms_faq_imported", request, user.id,
                       "%d question(s)%s" % (len(data.items), " (remplacement)" if data.replace else ""))
    return {"imported": len(data.items)}
