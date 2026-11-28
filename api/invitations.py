"""
api/invitations.py — Invitations digitales à QR code (événements privés).

Back-office (offre accompagnée) — capability invitations_manage (admin + commercial) :
  /admin/invitations/*            événements privés, invités, envoi WhatsApp, suivi, agents
  activation / annulation de pack → admin uniquement (invitations_activate)
Public (sans compte, rate limit) :
  GET /invitations/pass/{token}               pass nominatif (jamais de téléphone)
  GET /invitations/pass/{token}/calendar.ics  ajout au calendrier
  GET /invitations/track/{token}              lien de suivi client : chiffres uniquement
  GET|POST /invitations/signup/{slug}         inscription libre (page /r/<slug>) : pass immédiat
Séances (événement sur plusieurs jours) : PUT /admin/invitations/events/{id}/sessions
Scanner (agent assigné / orga propriétaire) :
  GET  /invitations/offline/{event_id}        liste hors ligne (empreintes des jetons)
  POST /invitations/checkins/sync             entrées faites hors ligne
  (le scan en ligne passe par POST /scan, cf. api/tickets.py)
"""
import json
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import invitation_catalog as cat
from core import invitations as inv
from core import invitation_media as media
from core import sessions as sess
from core.config import settings
from core.database import get_db
from core.limiter import PUBLIC_IP_CEILING, hit_or_429, limiter, path_token_key, user_key
from core.permissions import (can_send_invitations, get_current_admin, is_admin, normalize_role, require_staff_function,
                              sees_all_sales)
from core.security import get_current_user, hash_pin, log_security_event
from models.event import Event
from models.invitation import (
    EventGuest, EventSession, GuestCheckin, InvDesignMedia, InvitationDesign, InvModele, InvModeleCadre, InvitationPack,
    InvitationPage, PrivateEventSettings,
)
from models.invitation_catalog import InvFormula
from models.site_content import SiteContent
from models.user import User
from schemas.invitation import (
    AgentIn, AgentManualIn, CoverIn, DesignPut, GuestImportIn, GuestPatch, ManualCheckinIn, OwnerIn, PackCancelIn,
    PageIn, PrivateEventIn, PrivateEventPatch, SessionsIn, SignupIn, SignupSettingsIn, SyncIn,
)
from core import inv_design as idesign
from schemas.invitation_catalog import OrderIn

router = APIRouter(tags=["invitations"])
manager = require_staff_function("commercial")   # admin + commercial (pas le CM)
sender = require_staff_function("envoi")         # admin + staff « envoi » : file des envois WhatsApp, rien d'autre


# ── sérialisation ────────────────────────────────────────────────────
def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _guest_status(g: EventGuest) -> str:
    if g.revoked_at:
        return "revoked"
    if g.seats_used >= g.seats:
        return "complete"
    if g.seats_used > 0:
        return "partial"
    if g.sent_at:
        return "printed" if g.paper else "sent"
    return "to_print" if g.paper else "pending"


def _guest_out(ev: Event, st: PrivateEventSettings, g: EventGuest, send: bool = False) -> dict:
    """send : lien WhatsApp prêt à envoyer — seulement pour qui envoie depuis le numéro GoEvent
    (admin, staff « envoi »). Un commercial ne l'a jamais : il demande l'envoi à l'équipe."""
    msg = inv.render_message(ev, st, g) if g.token else ""
    return {"id": g.id, "full_name": g.full_name, "phone": g.phone, "seats": g.seats, "seats_used": g.seats_used,
            "table_label": g.table_label, "category": g.category, "status": _guest_status(g),
            "sent_at": _iso(g.sent_at), "first_checkin_at": _iso(g.first_checkin_at),
            "last_checkin_at": _iso(g.last_checkin_at), "revoked_at": _iso(g.revoked_at),
            "pass_url": inv.pass_url(g.token) if g.token else None, "paper": g.paper,
            "wa_url": inv.wa_url(g.phone, msg) if send and g.token and not g.paper else None, "message": msg}


def _pack_out(p: InvitationPack) -> dict:
    """Commande figée (instantané) ; « ancien pack » si activé avant le catalogue (legacy = True)."""
    snap = p.snapshot or {}
    return {"id": p.id, "size": p.size, "service": p.service, "amount_fcfa": p.amount_fcfa, "status": p.status,
            "payment_channel": p.payment_channel, "payment_ref": p.payment_ref, "amount_received": p.amount_received,
            "activated_at": _iso(p.activated_at), "cancelled_at": _iso(p.cancelled_at),
            "cancel_reason": p.cancel_reason, "note": p.note, "legacy": p.snapshot is None,
            "formula_name": p.formula_name, "pack_name": (snap.get("pack") or {}).get("name"),
            "custom": p.custom, "custom_reason": (snap.get("pack") or {}).get("custom_reason"),
            "offered": p.offered, "offered_reason": snap.get("offered_reason"),
            "unit_price": p.unit_price if p.unit_price is not None else p.amount_fcfa,
            "options": snap.get("options") or [], "options_total": p.options_total or 0,
            "discount": snap.get("discount"), "discount_amount": p.discount_amount or 0,
            "agents_included": cat.order_agents(p), "features": cat.order_features(p),
            "vat": snap.get("vat"), "amount_ht": p.amount_ht, "amount_vat": p.amount_vat,
            "quote_id": p.quote_id, "owner_id": p.owner_id, "commission_base_fcfa": p.commission_base_fcfa,
            "commission_rate_bp": p.commission_rate_bp}


def _event_summaries(db: Session, rows: list, owners: Optional[dict] = None) -> list:
    """Résumés de plusieurs événements privés en un nombre FIXE de requêtes (chiffres, packs,
    comportements, formules chargés en lot : id.in_(...) + regroupement en mémoire), quel que soit
    le nombre d'événements. rows = [(Event, PrivateEventSettings)]."""
    ids = [ev.id for ev, _ in rows]
    stats = inv.stats_many(db, ids)
    quotas = inv.quota_many(db, ids)
    features = cat.features_many(db, [(ev.id, st.service_level) for ev, st in rows])
    labels = cat.formula_labels(db, [st.service_level for _, st in rows])
    out = []
    for ev, st in rows:
        o = (owners or {}).get(st.owner_id) if st.owner_id else None
        s = stats[ev.id]
        out.append({"id": ev.id, "title": ev.title, "event_date": _iso(ev.event_date),
                    "owner_id": st.owner_id, "owner_name": o.full_name if o else None,
                    "owner_code": o.sales_code if o else None, "source_quote_id": st.source_quote_id,
                    "event_date_label": inv.fmt_date_fr(ev.event_date), "location": ev.location,
                    "client_name": st.client_name, "service_level": st.service_level, "event_type": st.event_type,
                    "formula_name": labels.get(st.service_level) if st.service_level else None,
                    "quota": quotas[ev.id], "listed": s["expected"],     # places listées = places des pass non annulés
                    "features": features[ev.id], "stats": s,
                    "purged": bool(st.purged_at), "purge_after": _iso(st.purge_after),
                    "send_requested_at": _iso(st.send_requested_at)})
    return out


def _event_summary(db: Session, ev: Event, st: PrivateEventSettings, owners: Optional[dict] = None) -> dict:
    return _event_summaries(db, [(ev, st)], owners)[0]


def _scope(user: User, st: PrivateEventSettings) -> None:
    """Portefeuille (lot 4.5) : un commercial n'accède qu'aux événements dont il est propriétaire ;
    l'admin voit tout. Ressource d'autrui = 404 (pas d'énumération)."""
    if not sees_all_sales(user) and st.owner_id != user.id:
        raise HTTPException(404, "Événement privé introuvable")


def _load(db: Session, event_id: int, user: User, lock: bool = False):
    ev = inv.get_private_event(db, event_id, lock=lock)
    st = inv.get_settings(db, ev.id)
    _scope(user, st)
    return ev, st


def _load_writable(db: Session, event_id: int, user: User, lock: bool = False):
    """Comme _load, pour une ÉCRITURE de données de présentation (affiche, design…) : refusée après
    la purge J+30, sinon des données personnelles réapparaîtraient sans jamais être repurgées."""
    ev, st = _load(db, event_id, user, lock=lock)
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée (conservation de 30 jours dépassée).")
    return ev, st


def _guest(db: Session, guest_id: int, user: User, lock: bool = False):
    q = db.query(EventGuest).filter(EventGuest.id == guest_id)
    if lock:
        q = q.with_for_update()
    g = q.first()
    if not g:
        raise HTTPException(404, "Invité introuvable")
    ev, st = _load(db, g.event_id, user)
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée (conservation de 30 jours dépassée).")
    return g, ev, st


def _service(db: Session, v: Optional[str], current: Optional[str] = None) -> Optional[str]:
    """Formule de l'événement = code d'une formule ACTIVE du catalogue (inchangée : acceptée même archivée)."""
    if v is None or v == current:
        return v
    if not db.query(InvFormula.id).filter(InvFormula.code == v, InvFormula.status == "active").first():
        raise HTTPException(400, "Formule inconnue ou archivée.")
    return v


def _owner_for_new(db: Session, user: User, requested: Optional[int]) -> Optional[int]:
    """Propriétaire d'un nouveau dossier : le commercial lui-même ; l'admin peut choisir un
    commercial actif (None = maison)."""
    from core.sales import is_seller
    if not sees_all_sales(user):
        return user.id
    if not requested:
        return None
    o = db.query(User).filter(User.id == requested, User.is_active == True).first()  # noqa: E712
    if not o or not is_seller(o):
        raise HTTPException(400, "Propriétaire invalide : choisissez un commercial actif.")
    return o.id


def _phone(v: Optional[str]) -> Optional[str]:
    try:
        return inv.normalize_phone(v)
    except ValueError as e:
        raise HTTPException(400, "Téléphone : %s." % e)


# ════════════════════════════════════════════════════════════
# BACK-OFFICE — événements privés
# ════════════════════════════════════════════════════════════

def _sender_whatsapp(db: Session) -> Optional[str]:
    """Numéro WhatsApp GoEvent (CMS) affiché au back-office comme expéditeur attendu des pass."""
    for key in ("invitations.contact.whatsapp", "global.contact.whatsapp"):
        row = db.get(SiteContent, key)
        v = re.sub(r"\D", "", (row.value if row else "") or "")
        if 8 <= len(v) <= 15:
            return v
    return None


@router.get("/admin/invitations/meta")
def admin_meta(db: Session = Depends(get_db), user: User = Depends(manager)):
    c = cat.full_catalog(db, include_archived=False)
    return {"formulas": c["formulas"], "packs": [p for p in c["packs"] if p["available"]], "options": c["options"],
            "catalog_settings": c["settings"], "units": c["units"],
            "channels": inv.channels(), "default_message": inv.DEFAULT_MESSAGE,
            "sender_whatsapp": _sender_whatsapp(db),
            "message_vars": list(inv.MESSAGE_VARS), "can_activate": is_admin(user), "can_edit_catalog": is_admin(user),
            "event_types": list(inv.EVENT_TYPES), "can_send": can_send_invitations(user),
            "retention_days": inv.INVITATION_RETENTION_DAYS}


@router.get("/admin/invitations/events")
def admin_list_events(owner_id: Optional[int] = None, db: Session = Depends(get_db), user: User = Depends(manager)):
    """Admin : tous les événements (filtre ?owner_id=, 0 = maison). Commercial : les siens uniquement."""
    q = (db.query(Event, PrivateEventSettings)
         .join(PrivateEventSettings, PrivateEventSettings.event_id == Event.id)
         .filter(Event.visibility == "private", Event.is_active == True))  # noqa: E712
    if not sees_all_sales(user):
        q = q.filter(PrivateEventSettings.owner_id == user.id)
    elif owner_id is not None:
        q = q.filter(PrivateEventSettings.owner_id == owner_id if owner_id else PrivateEventSettings.owner_id.is_(None))
    rows = q.order_by(Event.event_date.desc()).limit(300).all()
    owners = {u.id: u for u in db.query(User).filter(User.id.in_({st.owner_id for _, st in rows if st.owner_id})).all()}
    return {"events": _event_summaries(db, rows, owners),
            "summary": cat.revenue_summary(db, None if sees_all_sales(user) else user.id)}


def create_private_event(db: Session, user: User, request: Request, *, title: str, event_date: datetime, location: str,
                         description: Optional[str], client_name: str, client_phone: Optional[str],
                         host_label: Optional[str], service_level: Optional[str], message_template: Optional[str],
                         owner_id: Optional[int], source_quote_id: Optional[int] = None,
                         event_type: Optional[str] = None) -> Event:
    """Création d'un événement privé (formulaire ou conversion d'un devis). Commit inclus."""
    date = inv.naive_utc(event_date)
    event_type = inv.check_event_type(event_type)
    if date < inv.utcnow() - timedelta(days=1):
        raise HTTPException(400, "La date de l'événement est déjà passée.")
    house = inv.get_house_organizer(db)
    ev = Event(title=title.strip(), description=(description or "").strip(), location=location.strip(),
               category="Privé", event_date=date, price=0, total_seats=0, seats_sold=0, ticket_tiers=[],
               is_active=True, visibility="private", organizer_id=house.id)
    db.add(ev)
    db.flush()
    st = PrivateEventSettings(event_id=ev.id, client_name=client_name.strip(), client_phone=_phone(client_phone),
                              host_label=(host_label or "").strip() or None,
                              message_template=inv.check_template(message_template),
                              service_level=_service(db, service_level), purge_after=inv.purge_after_for(date),
                              event_type=event_type,
                              created_by=user.id, owner_id=owner_id, source_quote_id=source_quote_id)
    db.add(st)
    db.commit()
    log_security_event(db, "invitation_event_created", request, user.id, "event %d « %s » (%s)%s" % (
        ev.id, ev.title[:60], st.client_name[:40], " depuis le devis #%d" % source_quote_id if source_quote_id else ""))
    return ev


@router.post("/admin/invitations/events")
@limiter.limit("30/minute")
def admin_create_event(request: Request, data: PrivateEventIn, db: Session = Depends(get_db),
                       user: User = Depends(manager)):
    ev = create_private_event(db, user, request, title=data.title, event_date=data.event_date, location=data.location,
                              description=data.description, client_name=data.client_name,
                              client_phone=data.client_phone, host_label=data.host_label,
                              service_level=data.service_level, message_template=data.message_template,
                              owner_id=_owner_for_new(db, user, data.owner_id), event_type=data.event_type)
    return admin_get_event(ev.id, db, user)


@router.get("/admin/invitations/events/{event_id}")
def admin_get_event(event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    packs = db.query(InvitationPack).filter(InvitationPack.event_id == ev.id).order_by(InvitationPack.id).all()
    agents = db.query(User).filter(User.role == "agent", User.agent_event_id == ev.id, User.is_active == True).all()  # noqa: E712
    owners = {st.owner_id: db.get(User, st.owner_id)} if st.owner_id else {}
    out = _event_summary(db, ev, st, owners)
    out.update({"description": ev.description, "client_phone": st.client_phone, "host_label": st.host_label,
                "message_template": st.message_template, "message_is_default": st.message_template is None,
                "default_message": inv.default_message(st),
                "track_url": inv.track_url(st.track_token) if st.track_token else None,
                "purge_after": _iso(st.purge_after), "packs": [_pack_out(p) for p in packs],
                "agents": [{"id": a.id, "full_name": a.full_name, "phone_number": a.phone_number} for a in agents],
                "agents_included": cat.agents_included_total(db, ev.id), "signup": _signup_out(db, ev, st),
                "design_status": db.query(InvitationDesign.status).filter(InvitationDesign.event_id == ev.id).scalar()})
    sessions = sess.list_sessions(db, ev.id)
    locked = {sid for (sid,) in db.query(GuestCheckin.session_id).filter(
        GuestCheckin.event_id == ev.id, GuestCheckin.session_id.isnot(None)).distinct().all()}
    out.update({"sessions": [dict(sess.session_out(x), locked=x.id in locked) for x in sessions],
                "dates_label": sess.dates_label(sessions), "cover": _cover_meta(db, ev.id),
                "public_description": _public_description(db, ev.id)})
    return out


# ── page d'inscription : affiche et description publique ─────────────
def _cover_meta(db: Session, event_id: int) -> Optional[dict]:
    row = db.query(InvitationPage.cover_sha, InvitationPage.cover_width, InvitationPage.cover_height,
                   InvitationPage.updated_at).filter(InvitationPage.event_id == event_id,
                                                     InvitationPage.cover_sha.isnot(None)).first()   # jamais cover_data ici
    return {"sha": row[0], "width": row[1], "height": row[2], "updated_at": _iso(row[3])} if row else None


def _public_description(db: Session, event_id: int) -> str:
    return db.query(InvitationPage.description).filter(InvitationPage.event_id == event_id).scalar() or ""


def _page_row(db: Session, event_id: int, user: User) -> InvitationPage:
    row = db.get(InvitationPage, event_id) or InvitationPage(event_id=event_id)
    row.updated_by, row.updated_at = user.id, inv.utcnow()
    db.add(row)
    return row


@router.put("/admin/invitations/events/{event_id}/page")
@limiter.limit("30/minute")
def admin_put_page(request: Request, event_id: int, data: PageIn, db: Session = Depends(get_db),
                   user: User = Depends(manager)):
    """Description PUBLIQUE (page d'inscription), distincte de la note interne de l'événement."""
    ev, st = _load_writable(db, event_id, user)
    text = "\n".join(inv._clean(x, 2000) or "" for x in (data.description or "").replace("\r", "").split("\n")).strip()
    _page_row(db, ev.id, user).description = text[:2000] or None
    db.commit()
    log_security_event(db, "invitation_page_updated", request, user.id, "event %d : description %d car." % (ev.id, len(text)))
    return {"public_description": _public_description(db, ev.id)}


@router.put("/admin/invitations/events/{event_id}/cover")
@limiter.limit("10/minute")
def admin_put_cover(request: Request, event_id: int, data: CoverIn, db: Session = Depends(get_db),
                    user: User = Depends(manager)):
    """Affiche : contrôlée puis ré-encodée en JPEG par le serveur (core/invitation_media.py)."""
    ev, st = _load_writable(db, event_id, user)
    jpeg, w, h, sha = media.normalize_upload(data.image)
    row = _page_row(db, ev.id, user)
    row.cover_data, row.cover_width, row.cover_height, row.cover_sha = jpeg, w, h, sha
    db.commit()
    media.og_image(jpeg, sha)   # aperçu de partage prêt avant le premier partage du lien
    log_security_event(db, "invitation_cover_updated", request, user.id, "event %d : %dx%d, %d Ko" % (ev.id, w, h, len(jpeg) // 1024))
    return {"cover": _cover_meta(db, ev.id)}


@router.delete("/admin/invitations/events/{event_id}/cover")
@limiter.limit("10/minute")
def admin_delete_cover(request: Request, event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    row = _page_row(db, ev.id, user)
    row.cover_data = row.cover_sha = row.cover_width = row.cover_height = None
    db.commit()
    log_security_event(db, "invitation_cover_removed", request, user.id, "event %d" % ev.id)
    return {"cover": None}


@router.get("/admin/invitations/events/{event_id}/cover.jpg")
def admin_get_cover(event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    data = db.query(InvitationPage.cover_data).filter(InvitationPage.event_id == ev.id).scalar()
    if not data:
        raise HTTPException(404, "Aucune affiche")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.put("/admin/invitations/events/{event_id}/sessions")
@limiter.limit("30/minute")
def admin_sessions(request: Request, event_id: int, data: SessionsIn, db: Session = Depends(get_db),
                   user: User = Depends(manager)):
    """Séances (liste complète). Une séance qui a déjà des entrées ne peut être ni supprimée ni
    déplacée (son nom et l'ouverture des portes restent modifiables). La date de l'événement
    suit la première séance ; la conservation des données suit la dernière."""
    ev, st = _load(db, event_id, user, lock=True)
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée (conservation de 30 jours dépassée).")
    current = {x.id: x for x in sess.list_sessions(db, ev.id)}
    locked = {sid for (sid,) in db.query(GuestCheckin.session_id).filter(
        GuestCheckin.event_id == ev.id, GuestCheckin.session_id.isnot(None)).distinct().all()}
    items = []
    for it in data.sessions:
        if it.id is not None and it.id not in current:
            raise HTTPException(400, "Séance inconnue : rechargez la page.")
        items.append({"id": it.id, "label": it.label, "starts_at": inv.naive_utc(it.starts_at),
                      "ends_at": inv.naive_utc(it.ends_at), "doors_before_min": it.doors_before_min})
    items = sess.validate(items)
    kept = {it["id"] for it in items if it["id"]}
    for sid in locked:
        if sid not in kept:
            raise HTTPException(409, "« %s » a déjà des entrées : elle ne peut pas être supprimée." % current[sid].label)
    for it in items:
        old = current.get(it["id"]) if it["id"] else None
        if old and old.id in locked and (old.starts_at, old.ends_at) != (it["starts_at"], it["ends_at"]):
            raise HTTPException(409, "« %s » a déjà des entrées : ses horaires ne peuvent plus changer." % old.label)
    for sid, old in current.items():
        if sid not in kept:
            db.delete(old)
    for it in items:
        row = current.get(it["id"]) if it["id"] else None
        if row is None:
            row = EventSession(event_id=ev.id)
            db.add(row)
        row.label, row.starts_at, row.ends_at, row.doors_before_min = (
            it["label"], it["starts_at"], it["ends_at"], it["doors_before_min"])
    if items:
        ev.event_date = items[0]["starts_at"]
    db.flush()
    if not st.purged_at:
        st.purge_after = inv.purge_after_event(db, ev)
    db.commit()
    log_security_event(db, "invitation_sessions_updated", request, user.id, "event %d : %d séance(s) %s" % (
        ev.id, len(items), " | ".join("%s %s→%s" % (i["label"], i["starts_at"], i["ends_at"]) for i in items)[:400]))
    return admin_get_event(ev.id, db, user)


def _signup_out(db: Session, ev: Event, st: PrivateEventSettings) -> dict:
    return {"open": st.signup_open, "slug": st.signup_slug, "url": inv.signup_url(st.signup_slug) if st.signup_slug else None,
            "category": st.signup_category, "default_category": inv.SIGNUP_DEFAULT_CATEGORY, "max": st.signup_max,
            "until": _iso(st.signup_until), "count": inv.signup_count(db, ev.id), "state": inv.signup_state(db, ev, st)}


@router.put("/admin/invitations/events/{event_id}/signup")
@limiter.limit("30/minute")
def admin_signup_settings(request: Request, event_id: int, data: SignupSettingsIn, db: Session = Depends(get_db),
                          user: User = Depends(manager)):
    """Inscription libre : ouvre / ferme la page publique /r/<slug> et règle ses plafonds."""
    ev, st = _load(db, event_id, user, lock=True)
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée (conservation de 30 jours dépassée).")
    slug = (data.slug or "").strip().lower() or None
    if slug and not inv.SIGNUP_SLUG_RE.match(slug):
        raise HTTPException(400, "Lien : 3 à 40 caractères, lettres minuscules, chiffres et tirets (ex. fonej-2026).")
    if slug and db.query(PrivateEventSettings.event_id).filter(
            PrivateEventSettings.signup_slug == slug, PrivateEventSettings.event_id != ev.id).first():
        raise HTTPException(409, "Ce lien est déjà utilisé par un autre événement : choisissez-en un autre.")
    if data.open and not slug:
        raise HTTPException(400, "Choisissez le lien de la page avant d'ouvrir les inscriptions.")
    if data.open and inv.quota(db, ev.id) <= 0:
        raise HTTPException(400, "Activez un pack avant d'ouvrir les inscriptions.")
    old = (st.signup_open, st.signup_slug)
    st.signup_open = data.open
    st.signup_slug = slug
    st.signup_category = inv._clean(data.category, 40)
    st.signup_max = data.max
    st.signup_until = inv.naive_utc(data.until)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ce lien est déjà utilisé par un autre événement : choisissez-en un autre.")
    log_security_event(db, "invitation_signup_settings", request, user.id, "event %d : %s/%s → %s/%s, max %s" % (
        ev.id, "ouvert" if old[0] else "fermé", old[1] or "-", "ouvert" if st.signup_open else "fermé",
        st.signup_slug or "-", st.signup_max or "pack"))
    return admin_get_event(ev.id, db, user)


@router.patch("/admin/invitations/events/{event_id}")
@limiter.limit("60/minute")
def admin_update_event(request: Request, event_id: int, data: PrivateEventPatch, db: Session = Depends(get_db),
                       user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    f = data.model_fields_set
    if "title" in f and data.title:
        ev.title = data.title.strip()
    if "location" in f and data.location:
        ev.location = data.location.strip()
    if "description" in f:
        ev.description = (data.description or "").strip()
    if "event_date" in f and data.event_date:
        ev.event_date = inv.naive_utc(data.event_date)
        if not st.purged_at:
            st.purge_after = inv.purge_after_event(db, ev)
    if "client_name" in f and data.client_name:
        st.client_name = data.client_name.strip()
    if "client_phone" in f:
        st.client_phone = _phone(data.client_phone)
    if "host_label" in f:
        st.host_label = (data.host_label or "").strip() or None
    if "service_level" in f and data.service_level:
        st.service_level = _service(db, data.service_level, st.service_level)
    if "event_type" in f:
        st.event_type = inv.check_event_type(data.event_type)
    if data.reset_message:
        st.message_template = None
    elif "message_template" in f:
        st.message_template = inv.check_template(data.message_template)
    db.commit()
    log_security_event(db, "invitation_event_updated", request, user.id, "event %d" % ev.id)
    return admin_get_event(ev.id, db, user)


@router.post("/admin/invitations/events/{event_id}/owner")
@limiter.limit("30/minute")
def admin_event_owner(request: Request, event_id: int, data: OwnerIn, db: Session = Depends(get_db),
                      user: User = Depends(get_current_admin)):
    """Réaffecte un événement privé (admin) : commercial actif ou maison (owner_id vide).
    Les ventes et commissions déjà créées restent au vendeur d'origine."""
    ev, st = _load(db, event_id, user, lock=True)
    old = st.owner_id
    st.owner_id = _owner_for_new(db, user, data.owner_id)
    db.query(InvitationDesign).filter(InvitationDesign.event_id == ev.id).update(
        {InvitationDesign.owner_id: st.owner_id}, synchronize_session=False)   # suit le dossier
    db.commit()
    log_security_event(db, "invitation_event_owner_changed", request, user.id,
                       "event %d : %s → %s" % (ev.id, old or "maison", st.owner_id or "maison"))
    return admin_get_event(ev.id, db, user)


# ── design visuel (lot 6) ────────────────────────────────────────────
def _bound_json(value, limit: int):
    """Borne la taille d'un JSON reçu (upload = donnée non fiable). None passe."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(400, "Format invalide")
    if len(json.dumps(value)) > limit:
        raise HTTPException(413, "Contenu trop volumineux")
    return value


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    raw = await file.read(max_bytes + 1)          # lecture bornée : jamais le fichier entier en mémoire
    if not raw:
        raise HTTPException(400, "Fichier vide")
    if len(raw) > max_bytes:
        raise HTTPException(413, "Fichier trop lourd (max %d Mo)" % (max_bytes // (1024 * 1024)))
    return raw


def _get_or_create_design(db: Session, ev, st) -> InvitationDesign:
    d = db.query(InvitationDesign).filter(InvitationDesign.event_id == ev.id).first()
    if not d:
        d = InvitationDesign(event_id=ev.id, owner_id=st.owner_id, status="brouillon")
        db.add(d)
        db.flush()
    return d


def _merge_skin(base: Optional[dict], over: Optional[dict]) -> dict:
    """Skin effectif = gabarit + palette ajustée. L'override (couleurs extraites de l'affiche)
    ne remplace QUE ce qu'il contient : la police et les ornements du gabarit restent."""
    out = dict(base or {})
    for k, v in (over or {}).items():
        out[k] = dict(out.get(k) or {}, **v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _design_champs(db: Session, modele_id: Optional[int]) -> dict:
    """Schéma de fiche du cadre du gabarit ; sans gabarit : union des cadres actifs (brouillon)."""
    if modele_id:
        row = (db.query(InvModeleCadre.champs, InvModeleCadre.famille).join(InvModele, InvModele.cadre_id == InvModeleCadre.id)
               .filter(InvModele.id == modele_id).first())
        if row:
            return idesign.champs_for(row[1], row[0])
    out = {}
    for (ch,) in db.query(InvModeleCadre.champs).filter(InvModeleCadre.status == "actif").all():
        out.update(ch or {})
    return idesign.champs_for(None, out)


def _design_admin_out(db: Session, ev, d: Optional[InvitationDesign]) -> dict:
    mod = db.get(InvModele, d.modele_id) if d and d.modele_id else None
    cad = db.get(InvModeleCadre, mod.cadre_id) if mod else None
    return {
        "event_id": ev.id,
        "status": d.status if d else "brouillon",
        "modele_id": d.modele_id if d else None,
        "modele": {"id": mod.id, "name": mod.name, "skin": mod.skin, "enveloppe": mod.enveloppe} if mod else None,
        "cadre": {"code": cad.code, "famille": cad.famille, "scenes": cad.scenes,
                  "champs": idesign.champs_for(cad.famille, cad.champs)} if cad else None,
        "skin": _merge_skin(mod.skin if mod else None, d.theme_override if d else None) if (mod or (d and d.theme_override)) else None,
        "theme_override": d.theme_override if d else None,
        "fiche": (d.fiche if d else None) or {},
        "vip": d.vip if d else None,
        "photo_crop": d.photo_crop if d else None,
        "has_photo": bool(d and d.photo_sha),
        "has_logo": bool(d and d.logo_sha),
        "photo_sha": d.photo_sha if d else None,
        "logo_sha": d.logo_sha if d else None,
        "media": [{"ref": "m%d" % i, "id": i, "sha": sha} for i, sha in db.query(InvDesignMedia.id, InvDesignMedia.sha)
                  .filter(InvDesignMedia.event_id == ev.id).order_by(InvDesignMedia.id).all()],
        "categories": _design_categories(db, ev.id),
    }


def _design_categories(db: Session, event_id: int) -> list:
    """Catégories de la liste (une requête GROUP BY) avec leur rendu : l'admin voit, avant
    l'envoi, la couleur de badge et qui reçoit l'enveloppe officielle. Aucun nom d'invité."""
    rows = (db.query(EventGuest.category, func.count(EventGuest.id))
            .filter(EventGuest.event_id == event_id, EventGuest.revoked_at.is_(None), EventGuest.purged.is_(False))
            .group_by(EventGuest.category).all())
    out = [dict(name=c, count=n, **idesign.category_style(c)) for c, n in rows]
    return sorted(out, key=lambda x: (-x["count"], x["name"] or ""))


@router.get("/admin/invitations/cadres")
def admin_cadres(famille: Optional[str] = Query(None, max_length=20),
                 event_type: Optional[str] = Query(None, max_length=40),
                 db: Session = Depends(get_db), user: User = Depends(manager)):
    """Catalogue des cadres (+ gabarits/skins). `event_type` remonte les cadres
    suggérés en tête (lookup suggest_for, pas d'IA, pas de reconstruction)."""
    qy = db.query(InvModeleCadre).filter(InvModeleCadre.status == "actif")
    if famille:
        qy = qy.filter(InvModeleCadre.famille == famille)
    cadres = qy.order_by(InvModeleCadre.famille, InvModeleCadre.sort_order).all()
    by_cadre = {}
    for m in (db.query(InvModele).filter(InvModele.cadre_id.in_([c.id for c in cadres]), InvModele.status == "actif")
              .order_by(InvModele.sort_order).all()):            # une requête pour tous les cadres (plus de N+1)
        by_cadre.setdefault(m.cadre_id, []).append(m)
    out = []
    for c in cadres:
        modeles = by_cadre.get(c.id, [])
        out.append({
            "code": c.code, "famille": c.famille, "name": c.name, "scenes": c.scenes,
            "champs": idesign.champs_for(c.famille, c.champs),
            "suggested": bool(event_type and c.suggest_for and event_type in c.suggest_for),
            "modeles": [{"id": m.id, "name": m.name, "skin": m.skin, "enveloppe": m.enveloppe} for m in modeles],
        })
    out.sort(key=lambda x: (not x["suggested"], x["famille"]))
    return {"cadres": out}


@router.get("/admin/invitations/events/{event_id}/design")
def admin_get_design(event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    d = db.query(InvitationDesign).filter(InvitationDesign.event_id == ev.id).first()
    return _design_admin_out(db, ev, d)


@router.get("/admin/invitations/events/{event_id}/design/photo")
def admin_design_photo_view(event_id: int, which: str = Query("photo", max_length=10),
                            db: Session = Depends(get_db), user: User = Depends(manager)):
    """Sert la photo/logo stockée pour l'aperçu/recadrage côté admin (cloisonné owner)."""
    ev, st = _load(db, event_id, user)
    d = db.query(InvitationDesign).filter(InvitationDesign.event_id == ev.id).first()
    data, mime = (None, None)
    if d:
        data, mime = (d.logo_data, d.logo_mime) if which == "logo" else (d.photo_data, d.photo_mime)
    if not data:
        raise HTTPException(404, "Aucune image")
    return Response(bytes(data), media_type=mime or "image/jpeg", headers={"Cache-Control": "no-store"})


@router.put("/admin/invitations/events/{event_id}/design")
@limiter.limit("60/minute")
def admin_put_design(request: Request, event_id: int, data: DesignPut,
                     db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load_writable(db, event_id, user)
    d = _get_or_create_design(db, ev, st)
    f = data.model_fields_set
    if "modele_id" in f:
        if data.modele_id is not None:
            m = db.get(InvModele, data.modele_id)
            if not m or m.status != "actif":
                raise HTTPException(400, "Gabarit invalide")
            d.modele_id = m.id
        else:
            d.modele_id = None
    try:
        if "fiche" in f:
            d.fiche = idesign.sanitize_fiche(_bound_json(data.fiche, 20000), _design_champs(db, d.modele_id),
                                             _media_refs(db, ev.id))
            _drop_orphan_media(db, ev.id, d.fiche)
        if "theme_override" in f:
            d.theme_override = idesign.sanitize_theme(_bound_json(data.theme_override, 4000))
        if "vip" in f:
            d.vip = idesign.sanitize_vip(_bound_json(data.vip, 2000))
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    if "photo_crop" in f:
        d.photo_crop = data.photo_crop.model_dump() if data.photo_crop else None
    if "status" in f and data.status:
        if data.status == "pret" and not d.modele_id:
            raise HTTPException(400, "Choisissez un gabarit avant de publier le design")
        d.status = data.status
    d.owner_id = st.owner_id             # toujours celui du dossier (le cloisonnement passe par _load)
    d.updated_by = user.id
    d.updated_at = inv.utcnow()
    db.commit()
    log_security_event(db, "invitation_design_updated", request, user.id, "event %d" % ev.id)
    return admin_get_design(ev.id, db, user)


@router.post("/admin/invitations/events/{event_id}/design/palette")
@limiter.limit("20/minute")
async def admin_design_palette(request: Request, event_id: int, file: UploadFile = File(...),
                               db: Session = Depends(get_db), user: User = Depends(manager)):
    """Upload affiche → extraction COULEURS (Pillow). L'affiche n'est PAS stockée.
    L'admin applique/ajuste puis enregistre via theme_override (PUT)."""
    ev, st = _load_writable(db, event_id, user)
    raw = await _read_upload(file, idesign.MAX_UPLOAD_BYTES)
    try:
        tokens = idesign.extract_palette(raw)
    except idesign.ImageRejected as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Image illisible : envoyez un JPEG, PNG ou WebP valide")
    return {"couleurs": tokens}


@router.post("/admin/invitations/events/{event_id}/design/photo")
@limiter.limit("20/minute")
async def admin_design_photo(request: Request, event_id: int, file: UploadFile = File(...),
                             db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load_writable(db, event_id, user)
    d = _get_or_create_design(db, ev, st)
    raw = await _read_upload(file, idesign.MAX_UPLOAD_BYTES)
    try:
        b, mime, sha, w, h = idesign.reencode_image(raw, "photo")
    except idesign.ImageRejected as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Image illisible : envoyez un JPEG, PNG ou WebP valide")
    d.photo_data, d.photo_mime, d.photo_sha = b, mime, sha
    d.updated_by, d.updated_at = user.id, inv.utcnow()
    log_security_event(db, "invitation_design_photo", request, user.id, "event %d : photo client (%dx%d)" % (ev.id, w, h))
    db.commit()
    return {"ok": True, "photo_sha": sha, "width": w, "height": h}


@router.post("/admin/invitations/events/{event_id}/design/logo")
@limiter.limit("20/minute")
async def admin_design_logo(request: Request, event_id: int, file: UploadFile = File(...),
                            db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load_writable(db, event_id, user)
    d = _get_or_create_design(db, ev, st)
    raw = await _read_upload(file, idesign.MAX_UPLOAD_BYTES)
    try:
        b, mime, sha, w, h = idesign.reencode_image(raw, "logo")
    except idesign.ImageRejected as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Image illisible : envoyez un JPEG, PNG ou WebP valide")
    d.logo_data, d.logo_mime, d.logo_sha = b, mime, sha
    d.updated_by, d.updated_at = user.id, inv.utcnow()
    log_security_event(db, "invitation_design_logo", request, user.id, "event %d : logo (%dx%d)" % (ev.id, w, h))
    db.commit()
    return {"ok": True, "logo_sha": sha, "width": w, "height": h}


@router.post("/admin/invitations/events/{event_id}/design/import-text")
@limiter.limit("20/minute")
async def admin_design_import_text(request: Request, event_id: int, file: UploadFile = File(...),
                                   db: Session = Depends(get_db), user: User = Depends(manager)):
    """Lit un .txt, .docx ou .xlsx et renvoie son texte (colonnes séparées par des tabulations)
    pour pré-remplir un champ liste (programme, intervenants…). Aucune interprétation : l'admin
    relit puis Enregistre. .docx / .xlsx : même lecteur que l'import des invités
    (core/invitations.file_to_text : 2 Mo, garde anti zip-bomb, anciens .doc/.xls refusés)."""
    ev, st = _load_writable(db, event_id, user)  # cloisonnement + refus après purge
    raw = await _read_upload(file, inv.MAX_FILE_BYTES)
    name = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()
    if name.endswith((".docx", ".xlsx", ".xlsm")):
        text = inv.file_to_text(name, raw)["text"]
    elif "wordprocessingml" in ctype or "spreadsheetml" in ctype:
        text = inv.file_to_text("fichier." + ("docx" if "wordprocessingml" in ctype else "xlsx"), raw)["text"]
    elif name.endswith(".txt") or ctype == "text/plain":
        if b"\x00" in raw[:4096]:
            raise HTTPException(400, "Fichier texte illisible.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text.replace("\r\n", "\n").replace("\r", "\n"))
    else:
        raise HTTPException(400, "Formats acceptés : .docx, .xlsx ou .txt")
    text = text[:20000]
    log_security_event(db, "invitation_design_import", request, user.id,
                       "event %d : import %s (%d car.)" % (ev.id, (name or ctype or "fichier")[:60], len(text)))
    db.commit()
    return {"text": text}


# ── photos des intervenants (inv_design_media) ───────────────────────
MAX_DESIGN_MEDIA = 30          # par événement (intervenants affichés : 6 au plus par cadre, marge pour l'admin)


def _media_refs(db: Session, event_id: int) -> set:
    return {"m%d" % i for (i,) in db.query(InvDesignMedia.id).filter(InvDesignMedia.event_id == event_id).all()}


def _fiche_refs(fiche: Optional[dict]) -> set:
    out = set()
    for rows in (fiche or {}).values():
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict):
                    out.update(v for k, v in r.items() if k in ("photo", "logo") and isinstance(v, str))
    return out


def _drop_orphan_media(db: Session, event_id: int, fiche: Optional[dict]) -> None:
    """Images plus référencées par la fiche enregistrée et téléversées il y a plus d'1 h : supprimées
    (marge d'1 h : photo téléversée mais fiche pas encore enregistrée)."""
    used = _fiche_refs(fiche)
    old = inv.utcnow() - timedelta(hours=1)
    ids = [i for (i,) in db.query(InvDesignMedia.id).filter(InvDesignMedia.event_id == event_id,
                                                             InvDesignMedia.created_at < old).all()
           if "m%d" % i not in used]
    if ids:
        db.query(InvDesignMedia).filter(InvDesignMedia.id.in_(ids)).delete(synchronize_session=False)


def _media_row(db: Session, event_id: int, media_id: int) -> InvDesignMedia:
    m = db.query(InvDesignMedia).filter(InvDesignMedia.id == media_id, InvDesignMedia.event_id == event_id).first()
    if not m:
        raise HTTPException(404, "Image introuvable")
    return m


@router.post("/admin/invitations/events/{event_id}/design/media")
@limiter.limit("30/minute")
async def admin_design_media_upload(request: Request, event_id: int, file: UploadFile = File(...),
                                    db: Session = Depends(get_db), user: User = Depends(manager)):
    """Photo d'intervenant : ré-encodée (JPEG 500 px), renvoie sa référence « m<id> » à placer
    dans fiche.intervenants[].photo (enregistrée avec la fiche)."""
    ev, st = _load_writable(db, event_id, user)
    n = db.query(InvDesignMedia.id).filter(InvDesignMedia.event_id == ev.id).count()
    if n >= MAX_DESIGN_MEDIA:
        raise HTTPException(409, "%d photos maximum par événement : supprimez-en une." % MAX_DESIGN_MEDIA)
    raw = await _read_upload(file, idesign.MAX_UPLOAD_BYTES)
    try:
        b, mime, sha, w, h = idesign.reencode_image(raw, "intervenant")
    except idesign.ImageRejected as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Image illisible : envoyez un JPEG, PNG ou WebP valide")
    m = InvDesignMedia(event_id=ev.id, kind="intervenant", data=b, mime=mime, sha=sha, width=w, height=h,
                       created_by=user.id)
    db.add(m)
    db.flush()
    log_security_event(db, "invitation_design_media", request, user.id,
                       "event %d : photo intervenant m%d (%dx%d)" % (ev.id, m.id, w, h))
    db.commit()
    return {"ref": "m%d" % m.id, "id": m.id, "sha": sha, "width": w, "height": h}


@router.get("/admin/invitations/events/{event_id}/design/media/{media_id}")
def admin_design_media_view(event_id: int, media_id: int, db: Session = Depends(get_db),
                            user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    m = _media_row(db, ev.id, media_id)
    return Response(bytes(m.data), media_type=m.mime, headers={"Cache-Control": "private, no-store"})


@router.delete("/admin/invitations/events/{event_id}/design/media/{media_id}")
@limiter.limit("60/minute")
def admin_design_media_delete(request: Request, event_id: int, media_id: int, db: Session = Depends(get_db),
                              user: User = Depends(manager)):
    """Supprime l'image et la retire de la fiche (jamais de référence morte côté invité)."""
    ev, st = _load(db, event_id, user)
    m = _media_row(db, ev.id, media_id)
    ref = "m%d" % m.id
    db.delete(m)
    d = db.query(InvitationDesign).filter(InvitationDesign.event_id == ev.id).first()
    if d and d.fiche and ref in _fiche_refs(d.fiche):
        fiche = json.loads(json.dumps(d.fiche))
        for rows in fiche.values():
            if isinstance(rows, list):
                for r in rows:
                    if isinstance(r, dict):
                        for k in ("photo", "logo"):
                            if r.get(k) == ref:
                                r.pop(k)
        d.fiche = fiche
    log_security_event(db, "invitation_design_media_deleted", request, user.id, "event %d : %s supprimée" % (ev.id, ref))
    db.commit()
    return {"ok": True}


@router.delete("/admin/invitations/events/{event_id}/design/{which}")
@limiter.limit("30/minute")
def admin_design_del_image(request: Request, event_id: int, which: str,
                           db: Session = Depends(get_db), user: User = Depends(manager)):
    if which not in ("photo", "logo"):
        raise HTTPException(404, "Ressource inconnue")
    ev, st = _load(db, event_id, user)
    d = db.query(InvitationDesign).filter(InvitationDesign.event_id == ev.id).first()
    if d:
        if which == "photo":
            d.photo_data = d.photo_mime = d.photo_sha = None
        else:
            d.logo_data = d.logo_mime = d.logo_sha = None
        d.updated_by, d.updated_at = user.id, inv.utcnow()
        log_security_event(db, "invitation_design_image_deleted", request, user.id, "event %d : %s supprimé" % (ev.id, which))
        db.commit()
    return {"ok": True}


# ── invités ──────────────────────────────────────────────────────────
@router.get("/admin/invitations/events/{event_id}/guests")
def admin_guests(event_id: int, q: Optional[str] = Query(None, max_length=80),
                 status: Optional[str] = Query(None, max_length=20), page: int = Query(1, ge=1),
                 limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    query = db.query(EventGuest).filter(EventGuest.event_id == ev.id)
    if q:
        like = "%%%s%%" % q.strip()
        query = query.filter(or_(EventGuest.full_name.ilike(like), EventGuest.phone.ilike(like),
                                 EventGuest.table_label.ilike(like), EventGuest.category.ilike(like)))
    if status == "pending":
        query = query.filter(EventGuest.sent_at.is_(None), EventGuest.revoked_at.is_(None))
    elif status == "sent":
        query = query.filter(EventGuest.sent_at.isnot(None), EventGuest.revoked_at.is_(None))
    elif status == "arrived":
        query = query.filter(EventGuest.seats_used > 0)
    elif status == "not_arrived":
        query = query.filter(EventGuest.seats_used == 0, EventGuest.revoked_at.is_(None))
    elif status == "revoked":
        query = query.filter(EventGuest.revoked_at.isnot(None))
    elif status == "paper":
        query = query.filter(EventGuest.paper == True)  # noqa: E712
    total = query.count()
    rows = query.order_by(EventGuest.full_name, EventGuest.id).offset((page - 1) * limit).limit(limit).all()
    pending_ids = [gid for (gid,) in db.query(EventGuest.id).filter(
        EventGuest.event_id == ev.id, EventGuest.sent_at.is_(None), EventGuest.revoked_at.is_(None),
        EventGuest.phone.isnot(None), EventGuest.token.isnot(None), EventGuest.paper == False  # noqa: E712
    ).order_by(EventGuest.full_name, EventGuest.id).all()]
    paper_pending = db.query(EventGuest.id).filter(
        EventGuest.event_id == ev.id, EventGuest.paper == True, EventGuest.revoked_at.is_(None),  # noqa: E712
        EventGuest.token.isnot(None), EventGuest.sent_at.is_(None)).count()
    paper_total = db.query(EventGuest.id).filter(
        EventGuest.event_id == ev.id, EventGuest.paper == True, EventGuest.revoked_at.is_(None),  # noqa: E712
        EventGuest.token.isnot(None)).count()
    return {"total": total, "page": page, "limit": limit, "guests": [_guest_out(ev, st, g, can_send_invitations(user)) for g in rows],
            "send_queue": pending_ids,
            # Pass jamais envoyés faute de numéro : à compléter ou à transmettre à la main (« Copier le lien »)
            "pending_without_phone": db.query(EventGuest.id).filter(
                EventGuest.event_id == ev.id, EventGuest.sent_at.is_(None), EventGuest.revoked_at.is_(None),
                EventGuest.phone.is_(None), EventGuest.token.isnot(None), EventGuest.paper == False).count(),  # noqa: E712
            "paper_pending": paper_pending, "paper_total": paper_total}


@router.get("/admin/invitations/guests/{guest_id}")
def admin_guest(guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, user)
    return _guest_out(ev, st, g, can_send_invitations(user))


@router.post("/admin/invitations/events/{event_id}/guests/import")
@limiter.limit("30/minute")
def admin_import(request: Request, event_id: int, data: GuestImportIn, db: Session = Depends(get_db),
                 user: User = Depends(manager)):
    ev, st = _load(db, event_id, user, lock=not data.dry_run)   # verrou : deux imports simultanés ne dépassent pas le quota
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée (conservation de 30 jours dépassée).")
    return inv.import_guests(db, ev, [r.model_dump() for r in data.rows], user, data.dry_run, request)


@router.post("/admin/invitations/events/{event_id}/guests/read-file")
@limiter.limit("30/minute")
async def admin_read_file(request: Request, event_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                          user: User = Depends(manager)):
    """Lit un .xlsx / .docx et renvoie son contenu tabulé (rien n'est importé ni stocké)."""
    _load(db, event_id, user)
    data = await file.read(inv.MAX_FILE_BYTES + 1)
    return inv.file_to_text(file.filename, data)


@router.patch("/admin/invitations/guests/{guest_id}")
@limiter.limit("120/minute")
def admin_update_guest(request: Request, guest_id: int, data: GuestPatch, db: Session = Depends(get_db),
                       user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, user, lock=True)
    f = data.model_fields_set
    if "full_name" in f and data.full_name:
        g.full_name = data.full_name.strip()
    if "phone" in f:
        phone = _phone(data.phone)
        if phone and db.query(EventGuest.id).filter(EventGuest.event_id == ev.id, EventGuest.phone == phone,
                                                    EventGuest.id != g.id, EventGuest.revoked_at.is_(None)).first():
            raise HTTPException(400, "Ce numéro est déjà dans la liste.")
        if phone != g.phone:
            inv.set_phone(g, phone)
    if "paper" in f and data.paper is not None:
        g.paper = data.paper
    if "seats" in f and data.seats:
        if data.seats < g.seats_used:
            raise HTTPException(400, "%d personne(s) déjà entrée(s) avec ce pass." % g.seats_used)
        q = inv.quota(db, ev.id)
        if q and not g.revoked_at and inv.seats_listed(db, ev.id, exclude_guest=g.id) + data.seats > q:
            raise HTTPException(400, "Dépasse le pack actif (%d personnes)." % q)
        g.seats = data.seats
    if "table_label" in f:
        g.table_label = (data.table_label or "").strip() or None
    if "category" in f:
        g.category = (data.category or "").strip() or None
    db.commit()
    _settle_send_request(db, ev.id, st, user, request)   # passé en papier / numéro retiré : file peut-être vide
    return _guest_out(ev, st, g, can_send_invitations(user))


@router.post("/admin/invitations/guests/{guest_id}/revoke")
@limiter.limit("120/minute")
def admin_revoke(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, user, lock=True)
    if not g.revoked_at:
        g.revoked_at = inv.utcnow()
        db.commit()
        log_security_event(db, "invitation_pass_revoked", request, user.id, "pass %d (event %d)" % (g.id, ev.id))
        _settle_send_request(db, ev.id, st, user, request)
    return _guest_out(ev, st, g, can_send_invitations(user))


@router.post("/admin/invitations/guests/{guest_id}/restore")
@limiter.limit("120/minute")
def admin_restore(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, user, lock=True)
    if g.revoked_at:
        q = inv.quota(db, ev.id)
        if q and inv.seats_listed(db, ev.id) + g.seats > q:
            raise HTTPException(400, "Dépasse le pack actif (%d personnes)." % q)
        if g.phone and db.query(EventGuest.id).filter(EventGuest.event_id == ev.id, EventGuest.phone == g.phone,
                                                      EventGuest.id != g.id, EventGuest.revoked_at.is_(None)).first():
            raise HTTPException(400, "Un autre pass actif utilise déjà ce numéro.")
        g.revoked_at = None
        db.commit()
        log_security_event(db, "invitation_pass_restored", request, user.id, "pass %d (event %d)" % (g.id, ev.id))
    return _guest_out(ev, st, g, can_send_invitations(user))


@router.post("/admin/invitations/guests/{guest_id}/regenerate")
@limiter.limit("60/minute")
def admin_regenerate(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, user, lock=True)
    tok = inv.new_token()
    g.token, g.token_hash, g.sent_at, g.sent_by = tok, inv.token_hash(tok), None, None
    db.commit()
    log_security_event(db, "invitation_pass_regenerated", request, user.id, "pass %d (event %d)" % (g.id, ev.id))
    return _guest_out(ev, st, g, can_send_invitations(user))


@router.post("/admin/invitations/guests/{guest_id}/sent")
@limiter.limit("600/minute")
def admin_mark_sent(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(sender)):
    """Marque le pass envoyé (premier envoi ou renvoi). Staff « envoi » : pass de la file demandée,
    ou renvoi d'un pass envoyé il y a moins de RESEND_HOURS. Dernier pass de la file → demande soldée."""
    g, ev, st = _send_guest(db, guest_id, user)
    inv.require_active(db, ev.id)
    if g.revoked_at:
        raise HTTPException(400, "Pass annulé.")
    if g.paper or not g.phone or not g.token:
        raise HTTPException(400, "Pass papier ou invité sans numéro : rien à envoyer sur WhatsApp.")
    resend = bool(g.sent_at)
    if not is_admin(user) and not st.send_requested_at and not (resend and g.sent_at >= _resend_cutoff()):
        raise HTTPException(404, "Invité introuvable")
    g.sent_at, g.sent_by = inv.utcnow(), user.id
    db.commit()
    if resend:
        log_security_event(db, "invitation_pass_resent", request, user.id, "pass %d (event %d)" % (g.id, ev.id))
    _settle_send_request(db, ev.id, st, user, request)
    return _guest_out(ev, st, g, can_send_invitations(user))


# ════════════════════════════════════════════════════════════
# ENVOI PAR L'ÉQUIPE GoEvent — le commercial demande, l'admin / le staff « envoi » envoie
# ════════════════════════════════════════════════════════════

def _pending_q(db: Session, event_ids):
    """Pass à envoyer par WhatsApp : non envoyés, non annulés, avec numéro, hors pass papier."""
    return db.query(EventGuest).filter(
        EventGuest.event_id.in_(event_ids), EventGuest.sent_at.is_(None), EventGuest.revoked_at.is_(None),
        EventGuest.phone.isnot(None), EventGuest.token.isnot(None), EventGuest.paper == False)  # noqa: E712


RESEND_HOURS = 24   # le staff « envoi » peut renvoyer ce qu'il a envoyé (WhatsApp en échec, mauvais compte…)


def _resend_cutoff() -> datetime:
    return inv.utcnow() - timedelta(hours=RESEND_HOURS)


def _recent_sent_q(db: Session, event_ids, by_user: Optional[int] = None):
    """Pass envoyés sur WhatsApp depuis moins de RESEND_HOURS (renvoi possible). Inscrits en ligne
    exclus (pass remis à l'inscription, jamais envoyé par l'équipe). event_ids None = tous."""
    q = db.query(EventGuest).filter(
        EventGuest.sent_at >= _resend_cutoff(), EventGuest.revoked_at.is_(None),
        EventGuest.phone.isnot(None), EventGuest.token.isnot(None), EventGuest.paper == False,  # noqa: E712
        or_(EventGuest.source.is_(None), EventGuest.source != "signup"))
    if event_ids is not None:
        q = q.filter(EventGuest.event_id.in_(event_ids))
    return q.filter(EventGuest.sent_by == by_user) if by_user else q


def _settle_send_request(db: Session, event_id: int, st: PrivateEventSettings, user: User, request=None) -> None:
    """Plus aucun pass à envoyer → la demande d'envoi est soldée (effacée). Des invités ajoutés
    ensuite n'entrent dans la file de l'équipe qu'après une NOUVELLE demande du commercial."""
    if not st.send_requested_at or _pending_q(db, [event_id]).count():
        return
    n = db.query(func.count(EventGuest.id)).filter(
        EventGuest.event_id == event_id, EventGuest.sent_at >= st.send_requested_at, EventGuest.paper == False,  # noqa: E712
        or_(EventGuest.source.is_(None), EventGuest.source != "signup")).scalar()
    st.send_requested_at, st.send_requested_by = None, None
    db.commit()
    log_security_event(db, "invitation_send_completed", request, user.id,
                       "event %d : file vide, demande soldée (%d pass envoyés depuis la demande)" % (event_id, n))


def _send_scope(db: Session, event_id: int, user: User, lock: bool = False):
    """Staff « envoi » : événements dont l'envoi est DEMANDÉ, ou où il a envoyé des pass depuis
    moins de RESEND_HOURS (renvois) ; sinon 404, pas d'énumération. Admin : tous. Jamais après la purge."""
    ev = inv.get_private_event(db, event_id, lock=lock)
    st = inv.get_settings(db, ev.id)
    if not is_admin(user) and not st.send_requested_at and not _recent_sent_q(db, [ev.id], user.id).first():
        raise HTTPException(404, "Événement privé introuvable")
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée (conservation de 30 jours dépassée).")
    return ev, st


def _send_guest(db: Session, guest_id: int, user: User):
    g = db.query(EventGuest).filter(EventGuest.id == guest_id).with_for_update().first()
    if not g:
        raise HTTPException(404, "Invité introuvable")
    ev, st = _send_scope(db, g.event_id, user)
    return g, ev, st


def _mask(phone: Optional[str]) -> str:
    return ("… " + phone[-2:]) if phone and len(phone) > 2 else ""


@router.post("/admin/invitations/events/{event_id}/send-request")
@limiter.limit("30/minute")
def admin_send_request(request: Request, event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    """Le commercial (ou l'admin) confie l'envoi des pass à l'équipe GoEvent."""
    ev, st = _load(db, event_id, user, lock=True)
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée (conservation de 30 jours dépassée).")
    inv.require_active(db, ev.id)
    if not _pending_q(db, [ev.id]).count():
        raise HTTPException(400, "Aucun pass à envoyer par WhatsApp (tous envoyés, ou invités sans numéro).")
    if not st.send_requested_at:
        st.send_requested_at, st.send_requested_by = inv.utcnow(), user.id
        db.commit()
        log_security_event(db, "invitation_send_requested", request, user.id, "event %d" % ev.id)
    return {"send_requested_at": _iso(st.send_requested_at)}


@router.delete("/admin/invitations/events/{event_id}/send-request")
@limiter.limit("30/minute")
def admin_send_request_cancel(request: Request, event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user, lock=True)
    if st.send_requested_at:
        st.send_requested_at, st.send_requested_by = None, None
        db.commit()
        log_security_event(db, "invitation_send_request_cancelled", request, user.id, "event %d" % ev.id)
    return {"send_requested_at": None}


@router.get("/admin/invitations/sends")
def admin_sends(db: Session = Depends(get_db), user: User = Depends(sender)):
    """File des envois : événements dont l'envoi est demandé (pass à envoyer), puis ceux où cet
    utilisateur a envoyé des pass depuis moins de RESEND_HOURS (renvois). Rien d'autre (ni prix,
    ni client au-delà du nom, ni recettes)."""
    recent = dict(_recent_sent_q(db, None, user.id).with_entities(EventGuest.event_id, func.count(EventGuest.id))
                  .group_by(EventGuest.event_id).all())
    rows = (db.query(Event, PrivateEventSettings)
            .join(PrivateEventSettings, PrivateEventSettings.event_id == Event.id)
            .filter(Event.visibility == "private", Event.is_active == True,  # noqa: E712
                    PrivateEventSettings.purged_at.is_(None),
                    or_(PrivateEventSettings.send_requested_at.isnot(None), Event.id.in_(list(recent) or [0])))
            .order_by(PrivateEventSettings.send_requested_at.asc().nulls_last(), Event.id).limit(200).all())
    requested = [ev.id for ev, st in rows if st.send_requested_at]
    counts = dict(_pending_q(db, requested).with_entities(EventGuest.event_id, func.count(EventGuest.id))
                  .group_by(EventGuest.event_id).all()) if requested else {}
    by = {u.id: u.full_name for u in db.query(User).filter(User.id.in_({st.send_requested_by for _, st in rows if st.send_requested_by})).all()}
    return {"sender_whatsapp": _sender_whatsapp(db), "resend_hours": RESEND_HOURS, "events": [
        {"id": ev.id, "title": ev.title, "event_date_label": inv.fmt_date_fr(ev.event_date), "location": ev.location,
         "host_label": st.host_label or st.client_name, "pending": counts.get(ev.id, 0), "recent_sent": recent.get(ev.id, 0),
         "requested_at": _iso(st.send_requested_at), "requested_by": by.get(st.send_requested_by)}
        for ev, st in rows if counts.get(ev.id) or recent.get(ev.id)]}


@router.get("/admin/invitations/sends/{event_id}")
def admin_send_event(event_id: int, db: Session = Depends(get_db), user: User = Depends(sender)):
    """Pass à envoyer d'un événement (si l'envoi est demandé) et pass envoyés depuis moins de
    RESEND_HOURS (renvoi) : nom, places, numéro masqué à l'écran, lien WhatsApp prêt (message compris).
    Le lien contient le numéro complet : WhatsApp en a besoin (pas d'API d'envoi)."""
    ev, st = _send_scope(db, event_id, user)
    inv.require_active(db, ev.id)

    def item(g):
        return {"id": g.id, "full_name": g.full_name, "seats": g.seats, "phone_hint": _mask(g.phone),
                "wa_url": inv.wa_url(g.phone, inv.render_message(ev, st, g)), "sent_at": _iso(g.sent_at)}
    pend = (_pending_q(db, [ev.id]).order_by(EventGuest.full_name, EventGuest.id).limit(2000).all()
            if st.send_requested_at or is_admin(user) else [])
    recent = _recent_sent_q(db, [ev.id]).order_by(EventGuest.sent_at.desc(), EventGuest.id).limit(500).all()
    sent = db.query(func.count(EventGuest.id)).filter(EventGuest.event_id == ev.id, EventGuest.sent_at.isnot(None),
                                                      EventGuest.paper == False).scalar()  # noqa: E712
    return {"event": {"id": ev.id, "title": ev.title, "event_date_label": inv.fmt_date_fr(ev.event_date),
                      "location": ev.location, "host_label": st.host_label or st.client_name},
            "sender_whatsapp": _sender_whatsapp(db), "requested": bool(st.send_requested_at),
            "pending": [item(g) for g in pend], "recent": [item(g) for g in recent],
            "resend_hours": RESEND_HOURS, "sent": sent}


@router.delete("/admin/invitations/guests/{guest_id}")
@limiter.limit("120/minute")
def admin_delete_guest(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, user, lock=True)
    if g.seats_used > 0:
        raise HTTPException(400, "Cet invité est déjà entré : annulez le pass plutôt que de le supprimer.")
    db.query(GuestCheckin).filter(GuestCheckin.guest_id == g.id).delete(synchronize_session=False)
    db.delete(g)
    db.commit()
    log_security_event(db, "invitation_guest_deleted", request, user.id, "pass %d (event %d)" % (guest_id, ev.id))
    _settle_send_request(db, ev.id, st, user, request)
    return {"deleted": guest_id}


@router.post("/admin/invitations/guests/{guest_id}/checkin")
@limiter.limit("120/minute", key_func=user_key)
def admin_manual_checkin(request: Request, guest_id: int, data: ManualCheckinIn, db: Session = Depends(get_db),
                         user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, user)
    inv.require_active(db, ev.id)
    g = inv.manual_checkin(db, user, g, data.count, data.reason.strip(), request)
    return _guest_out(ev, st, g, can_send_invitations(user))


@router.get("/admin/invitations/events/{event_id}/paper.pdf")
@limiter.limit("20/minute")
def admin_paper_pdf(request: Request, event_id: int, scope: str = Query("paper", pattern="^(paper|all|pending)$"),
                    db: Session = Depends(get_db), user: User = Depends(manager)):
    """PDF A4 des pass à imprimer (8 par page, découpables). scope : paper (pass papier), pending
    (pass papier pas encore imprimés), all (tous les pass actifs). Les pass papier inclus sont marqués remis."""
    ev, st = _load(db, event_id, user)
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée.")
    inv.require_active(db, ev.id)
    q = db.query(EventGuest).filter(EventGuest.event_id == ev.id, EventGuest.revoked_at.is_(None),
                                    EventGuest.token.isnot(None))
    if scope == "paper":
        q = q.filter(EventGuest.paper == True)  # noqa: E712
    elif scope == "pending":
        q = q.filter(EventGuest.paper == True, EventGuest.sent_at.is_(None))  # noqa: E712
    guests = q.order_by(EventGuest.table_label, EventGuest.full_name).all()
    pdf = inv.paper_pdf(ev, st, guests)
    now = inv.utcnow()
    for g in guests:
        if g.paper and not g.sent_at:
            g.sent_at, g.sent_by = now, user.id
    db.commit()
    log_security_event(db, "invitation_paper_pdf", request, user.id, "event %d : %d pass (%s)" % (ev.id, len(guests), scope))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="pass-papier-%d.pdf"' % ev.id, "Cache-Control": "no-store"})


@router.get("/admin/invitations/events/{event_id}/arrivals")
def admin_arrivals(event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    rows = (db.query(GuestCheckin, EventGuest.full_name, EventGuest.table_label)
            .join(EventGuest, EventGuest.id == GuestCheckin.guest_id)
            .filter(GuestCheckin.event_id == ev.id).order_by(GuestCheckin.scanned_at.desc()).limit(30).all())
    labels = {x.id: x.label for x in sess.list_sessions(db, ev.id)}
    return {"stats": inv.stats(db, ev.id), "breakdown": inv.breakdown(db, ev.id), "arrivals": [
        {"name": n, "table": t, "count": c.count, "source": c.source, "at": _iso(c.scanned_at), "conflict": c.conflict,
         "reentry": c.reentry, "session": labels.get(c.session_id)}
        for c, n, t in rows]}


def _csv_cell(v) -> str:
    """Cellule CSV sans injection de formule (noms saisis par des tiers, dont l'inscription libre)."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@router.get("/admin/invitations/events/{event_id}/report.csv")
@limiter.limit("20/minute")
def admin_report_csv(request: Request, event_id: int, db: Session = Depends(get_db),
                     user: User = Depends(get_current_admin)):
    """Bilan de l'événement (admin seul, journalisé) : synthèse par catégorie et par séance, puis
    liste nominative des invités présents HORS inscription en ligne (officiels importés).
    Séparateur « ; » (Excel en français), UTF-8 avec BOM. À télécharger avant la purge à J+30."""
    import csv
    import io
    ev, st = _load(db, event_id, user)
    sessions = sess.list_sessions(db, ev.id)
    b = inv.breakdown(db, ev.id)
    cols = b["columns"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    row = lambda *v: w.writerow([_csv_cell(x) for x in v])  # noqa: E731
    now_bg = inv.local(inv.utcnow())
    row("Bilan des entrées", ev.title)
    row("Lieu", ev.location or "")
    row("Dates", sess.dates_label(sessions) or inv.fmt_date_fr(ev.event_date))
    row("Client", st.client_name)
    row("Généré le", now_bg.strftime("%d/%m/%Y %H:%M") + " (heure de Bangui)")
    if st.purged_at:
        row("Attention", "Liste anonymisée (conservation de 30 jours dépassée) : seuls les chiffres restent.")
    row()
    row("SYNTHÈSE PAR CATÉGORIE")
    head = ["Catégorie", "Places attendues", "Pass", "Inscrits en ligne"]
    for col in cols:
        head += ["%s : entrées" % col["label"]] + (["%s : retours" % col["label"]] if sessions else [])
    row(*(head + ["Pass utilisés au moins une fois"]))
    for r in b["categories"] + [dict(b["total"], category="TOTAL")]:
        line = [r["category"], r["expected"], r["passes"], r["signups"]]
        for col in cols:
            line += [r["by"][col["key"]]["entered"]] + ([r["by"][col["key"]]["reentries"]] if sessions else [])
        row(*(line + [r["came"]]))
    row()
    row("INVITÉS PRÉSENTS (hors inscription en ligne)")
    usage = sess.usage_by_session(db, ev.id) if sessions else {}
    present = (db.query(EventGuest).filter(EventGuest.event_id == ev.id, EventGuest.first_checkin_at.isnot(None),
                                          or_(EventGuest.source.is_(None), EventGuest.source != "signup"))
               .order_by(EventGuest.category, EventGuest.full_name).all())
    row(*(["Nom", "Catégorie", "Table", "Places"] + ["%s : entrées" % x.label for x in sessions] +
          ["Première entrée", "Pass annulé"]))
    for g in present:
        per = [usage.get(g.id, {}).get(x.id, 0) for x in sessions]
        first = inv.local(g.first_checkin_at)
        row(*([g.full_name, g.category or inv.NO_CATEGORY, g.table_label or "", g.seats] + per +
              [first.strftime("%d/%m %H:%M") if first else "", "oui" if g.revoked_at else ""]))
    if not present:
        row("Aucun")
    log_security_event(db, "invitation_report_csv", request, user.id, "event %d : %d présents nominatifs" % (ev.id, len(present)))
    name = "bilan-%s.csv" % (re.sub(r"[^a-z0-9]+", "-", inv._clean(ev.title, 60).lower()).strip("-") or "evenement")
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name, "Cache-Control": "no-store"})


# ── lien de suivi client ─────────────────────────────────────────────
@router.post("/admin/invitations/events/{event_id}/track-link")
@limiter.limit("30/minute")
def admin_track_link(request: Request, event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    if not inv.quota(db, ev.id) or not cat.event_features(db, ev.id, st.service_level)["track_link"]:
        raise HTTPException(400, "Le lien de suivi n'est inclus dans aucun pack actif de cet événement.")
    st.track_token, st.track_created_at = secrets.token_urlsafe(24), inv.utcnow()
    db.commit()
    log_security_event(db, "invitation_track_link_created", request, user.id, "event %d" % ev.id)
    return {"track_url": inv.track_url(st.track_token)}


@router.delete("/admin/invitations/events/{event_id}/track-link")
@limiter.limit("30/minute")
def admin_track_revoke(request: Request, event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    st.track_token = None
    db.commit()
    log_security_event(db, "invitation_track_link_revoked", request, user.id, "event %d" % ev.id)
    return {"track_url": None}


# ── agents de contrôle ───────────────────────────────────────────────
@router.post("/admin/invitations/events/{event_id}/agents")
@limiter.limit("30/minute")
def admin_add_agent(request: Request, event_id: int, data: AgentIn, db: Session = Depends(get_db),
                    user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    if not data.pin_code.isdigit():
        raise HTTPException(400, "Le PIN doit contenir 6 chiffres.")
    phone = _phone(data.phone_number)
    if not phone.startswith("+236"):
        raise HTTPException(400, "Numéro centrafricain attendu (+236).")
    org = "GoEvent" if cat.event_features(db, ev.id, st.service_level)["onsite_agents"] else st.client_name[:100]
    u = db.query(User).filter(User.phone_number == phone).first()
    if u and u.is_active and normalize_role(u.role) != "agent":
        raise HTTPException(400, "Ce numéro est déjà utilisé par un autre type de compte.")
    if u and u.is_active and u.agent_event_id not in (None, ev.id) and u.org_name != "GoEvent":
        raise HTTPException(400, "Ce numéro appartient déjà à l'agent d'un autre organisateur.")
    if u:
        u.full_name, u.pin_hash, u.org_name, u.agent_event_id = data.full_name.strip(), hash_pin(data.pin_code), org, ev.id
        u.role, u.is_active, u.failed_login_attempts, u.locked_until = "agent", True, 0, None
    else:
        u = User(phone_number=phone, full_name=data.full_name.strip(), role="agent", org_name=org,
                 agent_event_id=ev.id, pin_hash=hash_pin(data.pin_code), is_active=True)
        db.add(u)
    db.commit()
    log_security_event(db, "invitation_agent_assigned", request, user.id, "agent %d → event %d" % (u.id, ev.id))
    return {"id": u.id, "full_name": u.full_name, "phone_number": u.phone_number}


@router.delete("/admin/invitations/events/{event_id}/agents/{agent_id}")
@limiter.limit("30/minute")
def admin_remove_agent(request: Request, event_id: int, agent_id: int, db: Session = Depends(get_db),
                       user: User = Depends(manager)):
    ev, st = _load(db, event_id, user)
    u = db.query(User).filter(User.id == agent_id, User.role == "agent", User.agent_event_id == ev.id).first()
    if not u:
        raise HTTPException(404, "Agent introuvable")
    u.is_active, u.agent_event_id = False, None
    db.commit()
    log_security_event(db, "invitation_agent_removed", request, user.id, "agent %d (event %d)" % (u.id, ev.id))
    return {"removed": agent_id}


# ── packs (activation : admin uniquement) ────────────────────────────
@router.post("/admin/invitations/events/{event_id}/packs")
@limiter.limit("20/minute")
def admin_activate_pack(request: Request, event_id: int, data: OrderIn, db: Session = Depends(get_db),
                        user: User = Depends(get_current_admin)):
    """Commande figée : même calcul que le devis (cat.quote). Montant reçu ≥ total, ou « Pack offert »."""
    ev, st = _load(db, event_id, user, lock=True)
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée.")
    quote_obj = None
    if data.quote_id:
        from core import quotes as qt
        quote_obj = qt.get_quote(db, data.quote_id, user, lock=True)
        if quote_obj.status == "converti" or quote_obj.pack_id:
            raise HTTPException(409, "Ce devis a déjà été converti.")
        if quote_obj.event_id and quote_obj.event_id != ev.id:
            raise HTTPException(400, "Ce devis est rattaché à un autre événement.")
        if quote_obj.needs_admin and not quote_obj.validated_at:
            raise HTTPException(400, "Devis à valider par l'administrateur avant l'activation.")
        if quote_obj.status not in ("envoye", "accepte") or qt.effective_status(quote_obj) == "expire":
            raise HTTPException(400, "Devis non activable (%s) : il doit être envoyé ou accepté, et valide."
                                % qt.STATUS_LABELS.get(qt.effective_status(quote_obj), quote_obj.status))
    owner = db.get(User, st.owner_id) if st.owner_id else None
    p = cat.activate(db, ev, user, True, data.model_dump(), inv.channels(), request, quote_obj=quote_obj, owner=owner)
    return _pack_out(p)


@router.post("/admin/invitations/packs/{pack_id}/cancel")
@limiter.limit("20/minute")
def admin_cancel_pack(request: Request, pack_id: int, data: PackCancelIn, db: Session = Depends(get_db),
                      user: User = Depends(get_current_admin)):
    p = db.query(InvitationPack).filter(InvitationPack.id == pack_id).with_for_update().first()
    if not p or p.status != "actif":
        raise HTTPException(404, "Pack actif introuvable")
    p.status, p.cancelled_by, p.cancelled_at, p.cancel_reason = "annule", user.id, inv.utcnow(), data.reason.strip()
    from core.sales import COMMISSION_SOURCE
    from models.commission import Commission
    com = (db.query(Commission).filter(Commission.source_type == COMMISSION_SOURCE, Commission.source_id == p.id)
           .with_for_update().first())
    paid_warning = None
    if com and com.status in ("pending", "approved"):
        com.status = "cancelled"
        com.note = ("Pack annulé : %s" % data.reason.strip())[:500]
    elif com and com.status == "paid":
        paid_warning = "Commission déjà versée (%d FCFA) : à régulariser avec le commercial." % com.amount_fcfa
    db.commit()
    log_security_event(db, "invitation_pack_cancelled", request, user.id,
                       "pack %d (event %d) : %s%s" % (p.id, p.event_id, data.reason[:150],
                                                       " · " + paid_warning if paid_warning else ""))
    out = _pack_out(p)
    out["commission_warning"] = paid_warning
    return out


@router.post("/admin/invitations/purge")
@limiter.limit("10/minute")
def admin_purge(request: Request, event_id: Optional[int] = None, db: Session = Depends(get_db),
                user: User = Depends(get_current_admin)):
    if event_id:
        inv.get_private_event(db, event_id)
    n = inv.purge_due(db, force_event_id=event_id)
    log_security_event(db, "invitation_purge", request, user.id, "%d invités anonymisés%s"
                       % (n, " (event %d, anticipé)" % event_id if event_id else ""))
    return {"anonymized": n}


# ════════════════════════════════════════════════════════════
# PUBLIC — pass invité, calendrier, suivi client
# ════════════════════════════════════════════════════════════

def _pass_guest(db: Session, token: str):
    if not inv._TOKEN_RE.match(token or ""):
        raise HTTPException(404, "Pass introuvable")
    g = db.query(EventGuest).filter(EventGuest.token == token).first()
    if not g:
        raise HTTPException(404, "Pass introuvable")
    ev = db.query(Event).filter(Event.id == g.event_id, Event.visibility == "private").first()
    st = db.query(PrivateEventSettings).filter(PrivateEventSettings.event_id == g.event_id).first()
    if not ev or not st:
        raise HTTPException(404, "Pass introuvable")
    return g, ev, st


def _no_store(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


def _design_payload(db: Session, event_id: int, token: str) -> Optional[dict]:
    """Couche visuelle (lot 6) pour la page invité. None = pas de design prêt
    → la page retombe sur le pass actuel (zéro régression). Une seule requête,
    jointure de 3 petites tables par clé ; route publique rate-limitée.
    Photo/logo servis derrière le pass_token (chemin racine, le front préfixe l'API)."""
    row = (
        db.query(InvitationDesign, InvModele, InvModeleCadre)
        .outerjoin(InvModele, InvModele.id == InvitationDesign.modele_id)
        .outerjoin(InvModeleCadre, InvModeleCadre.id == InvModele.cadre_id)
        .filter(InvitationDesign.event_id == event_id, InvitationDesign.status == "pret")
        .first()
    )
    if not row:
        return None
    d, mod, cad = row
    if not mod or not cad:               # design créé mais pas encore relié à un gabarit
        return None
    scenes = cad.scenes or {}
    q = "/invitations/pass/%s/" % token
    fiche = d.fiche or {}
    refs = _fiche_refs(fiche)
    if refs:                             # « m<id> » → URL versionnée (une requête, sans les octets)
        shas = {"m%d" % i: sha for i, sha in db.query(InvDesignMedia.id, InvDesignMedia.sha)
                .filter(InvDesignMedia.event_id == event_id).all()}
        fiche = json.loads(json.dumps(fiche))
        for rows in fiche.values():
            if isinstance(rows, list):
                for r in rows:
                    if isinstance(r, dict):
                        for k in ("photo", "logo"):
                            if k in r:
                                ref = r.pop(k)
                                if ref in shas:
                                    r[k] = "%smedia/%s?v=%s" % (q, ref[1:], shas[ref][:12])
    return {
        "cadre": cad.code,
        "famille": cad.famille,
        "scenes": scenes,
        "skin": _merge_skin(mod.skin, d.theme_override),   # gabarit + palette ajustée (police conservée)
        "fiche": fiche,
        "photo_url": (q + "photo?v=" + d.photo_sha) if d.photo_sha else (d.photo_url or None),
        "logo_url": (q + "logo?v=" + d.logo_sha) if d.logo_sha else (d.logo_url or None),
        "enveloppe": bool(mod.enveloppe and scenes.get("enveloppe")),
        "vip": d.vip or None,
    }


@router.get("/invitations/pass/{token}")
@limiter.limit("30/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_pass(request: Request, token: str, db: Session = Depends(get_db)):
    g, ev, st = _pass_guest(db, token)
    sessions = sess.list_sessions(db, ev.id)
    used = g.seats_used
    if sessions:   # séances : état du pass DANS la séance en cours (valable de nouveau à la suivante)
        cur = sess.find_session(sessions, inv.utcnow())
        used = sess.used_in_session(db, g.id, cur.id) if cur else 0
    if not ev.is_active:
        status = "cancelled"
    elif g.revoked_at:
        status = "revoked"
    elif inv.quota(db, ev.id) <= 0:
        status = "inactive"
    elif not used:
        status = "valid"
    else:
        status = "complete" if used >= g.seats else "partial"
    payload = {
        "status": status, "guest_name": g.full_name, "seats": g.seats, "seats_used": used,
        "table": g.table_label, "category": g.category, "event_title": ev.title, "event_date": _iso(ev.event_date),
        "event_date_label": inv.fmt_date_fr(ev.event_date), "location": ev.location,
        "host_label": st.host_label or st.client_name, "pass_url": inv.pass_url(g.token),
        "dates_label": sess.dates_label(sessions), "multi_session": len(sessions) > 1,
    }
    design = _design_payload(db, ev.id, token)
    if design:                           # couche visuelle lot 6 ; absente = pass classique
        design["cat"] = idesign.category_style(g.category)   # couleur d'accréditation + accueil officiel
        payload["design"] = design
    return _no_store(payload)


@router.get("/invitations/pass/{token}/preview")
@limiter.limit("30/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_pass_preview(request: Request, token: str, db: Session = Depends(get_db)):
    """Aperçu du lien envoyé sur WhatsApp (lu par web/api/og-pass.js pour les robots d'aperçu).
    L'ÉVÉNEMENT seulement : jamais le nom, le numéro ou la catégorie de l'invité, car l'aperçu
    passe par les serveurs de la messagerie. Pass annulé ou événement annulé : 404."""
    g, ev, st = _pass_guest(db, token)
    if g.revoked_at or not ev.is_active:
        raise HTTPException(404, "Pass introuvable")
    sessions = sess.list_sessions(db, ev.id)
    return JSONResponse({"event_title": ev.title, "when": sess.dates_label(sessions) or inv.fmt_date_fr(ev.event_date),
                         "location": ev.location, "host_label": st.host_label or st.client_name,
                         "famille": inv.event_famille(st.event_type)},
                        headers={"Cache-Control": "private, max-age=300", "X-Robots-Tag": "noindex"})


@router.get("/invitations/pass/{token}/media/{media_id}")
@limiter.limit("120/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_pass_media(request: Request, token: str, media_id: int, db: Session = Depends(get_db)):
    """Photo d'intervenant d'un design PRÊT, derrière le pass_token ; l'image doit appartenir à
    l'événement du pass (sinon 404 : pas d'énumération entre événements)."""
    g, ev, st = _pass_guest(db, token)
    if not db.query(InvitationDesign.event_id).filter(InvitationDesign.event_id == ev.id,
                                                      InvitationDesign.status == "pret").first():
        raise HTTPException(404, "Image introuvable")
    m = _media_row(db, ev.id, media_id)
    return Response(bytes(m.data), media_type=m.mime,
                    headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Robots-Tag": "noindex"})


def _serve_design_image(db: Session, token: str, which: str) -> Response:
    """Sert la photo/logo d'un design PRÊT, derrière le pass_token (non énumérable).
    Octets déjà ré-encodés à l'upload : aucune donnée d'origine servie."""
    g, ev, st = _pass_guest(db, token)
    d = (db.query(InvitationDesign)
         .filter(InvitationDesign.event_id == ev.id, InvitationDesign.status == "pret").first())
    data = mime = None
    if d:
        data, mime = (d.photo_data, d.photo_mime) if which == "photo" else (d.logo_data, d.logo_mime)
    if not data:
        raise HTTPException(404, "Image introuvable")
    return Response(bytes(data), media_type=mime or "image/jpeg",
                    # Photo du client = donnée personnelle : jamais en cache partagé (CDN) ; URL versionnée (?v=sha)
                    headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Robots-Tag": "noindex"})


@router.get("/invitations/pass/{token}/photo")
@limiter.limit("90/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_pass_photo(request: Request, token: str, db: Session = Depends(get_db)):
    return _serve_design_image(db, token, "photo")


@router.get("/invitations/pass/{token}/logo")
@limiter.limit("90/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_pass_logo(request: Request, token: str, db: Session = Depends(get_db)):
    return _serve_design_image(db, token, "logo")


def _ics(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@router.get("/invitations/pass/{token}/calendar.ics")
@limiter.limit("30/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_pass_ics(request: Request, token: str, db: Session = Depends(get_db)):
    g, ev, st = _pass_guest(db, token)
    start = ev.event_date or inv.utcnow()
    fmt = "%Y%m%dT%H%M%SZ"
    sessions = sess.list_sessions(db, ev.id)
    slots = [(x.starts_at, x.ends_at, "%s — %s" % (ev.title, x.label), x.id) for x in sessions] or \
        [(start, start + timedelta(hours=4), ev.title, None)]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GoEvent//Invitations//FR", "CALSCALE:GREGORIAN"]
    for a, b, title, sid in slots:   # une entrée d'agenda par séance
        lines += ["BEGIN:VEVENT", "UID:goevent-invitation-%d%s@goevent.africa" % (g.id, "-s%d" % sid if sid else ""),
                  "DTSTAMP:%s" % inv.utcnow().strftime(fmt), "DTSTART:%s" % a.strftime(fmt), "DTEND:%s" % b.strftime(fmt),
                  "SUMMARY:%s" % _ics(title), "LOCATION:%s" % _ics(ev.location or ""),
                  "DESCRIPTION:%s" % _ics("Votre pass (%s) : %s" % (inv.places_label(g.seats), inv.pass_url(g.token))),
                  "END:VEVENT"]
    body = "\r\n".join(lines + ["END:VCALENDAR", ""])
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="invitation.ics"', "Cache-Control": "no-store"})


@router.get("/invitations/track/{token}")
@limiter.limit("60/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_track(request: Request, token: str, db: Session = Depends(get_db)):
    if not inv._TOKEN_RE.match(token or ""):
        raise HTTPException(404, "Lien de suivi invalide ou révoqué")
    st = db.query(PrivateEventSettings).filter(PrivateEventSettings.track_token == token).first()
    ev = db.query(Event).filter(Event.id == st.event_id).first() if st else None
    if not st or not ev:
        raise HTTPException(404, "Lien de suivi invalide ou révoqué")
    s = inv.stats(db, ev.id)
    b = inv.breakdown(db, ev.id)
    cur = b["current"]
    # Chiffres uniquement : ni lieu, ni nom, ni numéro d'invité (catégories et séances : libellés du back-office).
    return JSONResponse({"event_title": ev.title, "event_date": _iso(ev.event_date),
                         "event_date_label": inv.fmt_date_fr(ev.event_date), "expected": s["expected"],
                         "entered": s["entered"], "remaining": s["remaining"], "rate": s["rate"],
                         "dates_label": sess.dates_label(sess.list_sessions(db, ev.id)),
                         "session": s["session"]["label"] if s["session"] else None,
                         "session_current": bool(s["session"] and s["session"]["current"]),
                         "sessions": [{"label": col["label"], "entered": b["total"]["by"][col["key"]]["entered"],
                                       "reentries": b["total"]["by"][col["key"]]["reentries"], "current": col["key"] == cur}
                                      for col in b["columns"]] if s["session"] else [],
                         "categories": [{"category": r["category"], "expected": r["expected"],
                                         "entered": r["by"][cur]["entered"]} for r in b["categories"]],
                         "updated_at": inv.utcnow().isoformat() + "Z"},
                        headers={"Cache-Control": "public, max-age=15", "X-Robots-Tag": "noindex"})


@router.get("/invitations/signup/{slug}")
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_signup_info(request: Request, slug: str, db: Session = Depends(get_db)):
    """Page d'inscription libre : événement + état (open | closed | ended | inactive | full).
    Pas de limite par lien : tout le public d'un événement ouvre la même page (CGNAT)."""
    ev, st = inv.signup_settings(db, slug)
    sessions = sess.list_sessions(db, ev.id)
    cover = _cover_meta(db, ev.id)
    base = "%s/invitations/signup/%s" % (settings.PUBLIC_API_URL.rstrip("/"), slug)
    return _no_store({"state": inv.signup_state(db, ev, st), "event_title": ev.title, "event_date": _iso(ev.event_date),
                      "event_date_label": inv.fmt_date_fr(ev.event_date), "location": ev.location,
                      "host_label": st.host_label or st.client_name,
                      "category": st.signup_category or inv.SIGNUP_DEFAULT_CATEGORY,
                      "retention_days": inv.INVITATION_RETENTION_DAYS,
                      "dates_label": sess.dates_label(sessions), "when": sess.when_parts(sessions, ev.event_date),
                      "multi_session": len(sessions) > 1, "description": _public_description(db, ev.id),
                      "cover_url": "%s/cover.jpg?v=%s" % (base, cover["sha"][:12]) if cover else None,
                      # f= : version du calcul de l'aperçu (change l'URL quand le rendu change → caches renouvelés)
                      "og_image_url": "%s/og.jpg?v=%s&f=2" % (base, cover["sha"][:12]) if cover else None,
                      "share_url": inv.signup_url(slug), "max_per_phone": inv.SIGNUP_MAX_PER_PHONE})


def _public_cover(db: Session, slug: str):
    ev, st = inv.signup_settings(db, slug)
    row = db.query(InvitationPage.cover_data, InvitationPage.cover_sha).filter(
        InvitationPage.event_id == ev.id, InvitationPage.cover_sha.isnot(None)).first()
    if not row:
        raise HTTPException(404, "Aucune affiche")
    return row


def _image(data: bytes, sha: str, v: Optional[str]) -> Response:
    # Lien versionné (?v=) : immuable, mis en cache par le navigateur et le CDN ; sinon 10 min.
    cache = "public, max-age=31536000, immutable" if v and sha.startswith(v) else "public, max-age=600"
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": cache, "ETag": '"%s"' % sha[:32],
                                                            "X-Content-Type-Options": "nosniff"})


@router.get("/invitations/signup/{slug}/cover.jpg")
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_signup_cover(request: Request, slug: str, v: Optional[str] = Query(None, max_length=64),
                        db: Session = Depends(get_db)):
    data, sha = _public_cover(db, slug)
    return _image(data, sha, v)


@router.get("/invitations/signup/{slug}/og.jpg")
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_signup_og(request: Request, slug: str, v: Optional[str] = Query(None, max_length=64),
                     db: Session = Depends(get_db)):
    """Aperçu de partage 1200×630 (WhatsApp, Facebook…) calculé depuis l'affiche."""
    data, sha = _public_cover(db, slug)
    return _image(media.og_image(data, sha), sha, v)


@router.post("/invitations/signup/{slug}")
@limiter.limit("60/minute")
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_signup(request: Request, slug: str, data: SignupIn, db: Session = Depends(get_db)):
    """Inscription libre : crée le pass (1 place) et le renvoie aussitôt. Sans compte.
    Limites : 10 / heure par numéro (3 personnes + erreurs de saisie), 60 / min par IP (large : CGNAT)."""
    if data.website:
        raise HTTPException(400, "Inscription impossible.")
    if not data.consent:
        raise HTTPException(400, "Cochez la case d'accord pour recevoir votre pass.")
    phone = _phone(data.phone)
    hit_or_429("10/hour", "signup", phone, "Trop de tentatives pour ce numéro. Réessayez dans une heure.")
    g, ev, st = inv.self_signup(db, slug, data.full_name, phone, request)
    link = inv.pass_url(g.token)
    return _no_store({"guest_name": g.full_name, "category": g.category, "event_title": ev.title,
                      "event_date_label": inv.fmt_date_fr(ev.event_date), "pass_url": link, "token": g.token,
                      "wa_url": inv.wa_url(phone, "Mon pass pour %s : %s" % (ev.title, link))})


# ════════════════════════════════════════════════════════════
# SCANNER — hors ligne
# ════════════════════════════════════════════════════════════

def _scanner_event(db: Session, event_id: int, user: User) -> Event:
    from api.tickets import _can_operate_event
    ev = inv.get_private_event(db, event_id)
    if _can_operate_event(user, ev) or normalize_role(user.role) == "admin":
        return ev
    # Commercial : uniquement les événements de son portefeuille (lot 4.5)
    if normalize_role(user.role) == "staff" and user.staff_function == "commercial" and not user.must_change_pin:
        if inv.get_settings(db, ev.id).owner_id == user.id:
            return ev
    raise HTTPException(403, "Accès réservé aux agents de cet événement.")


@router.get("/invitations/events/{event_id}/search")
@limiter.limit("120/minute", key_func=user_key)
def scanner_search(request: Request, event_id: int, q: str = Query("", max_length=60), db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Recherche par nom ou table (entrée manuelle au scanner). Jamais de numéro de téléphone."""
    ev = _scanner_event(db, event_id, user)
    inv.require_active(db, ev.id)
    return {"guests": inv.search_guests(db, ev, q)}


@router.post("/invitations/manual-checkin")
@limiter.limit("120/minute", key_func=user_key)
def scanner_manual_checkin(request: Request, data: AgentManualIn, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    if normalize_role(user.role) not in ("agent", "organizer"):
        raise HTTPException(403, "Réservé aux agents de contrôle.")
    return inv.agent_manual_checkin(db, user, data.guest_id, data.count, data.reason, data.last4,
                                    data.identity_checked, data.client_uuid, request)


@router.get("/invitations/offline/{event_id}")
@limiter.limit("10/minute", key_func=user_key)
def offline_list(request: Request, event_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    ev = _scanner_event(db, event_id, user)
    inv.require_active(db, ev.id)
    data = inv.offline_list(db, ev)
    log_security_event(db, "invitation_offline_list", request, user.id, "event %d : %d pass" % (ev.id, len(data["guests"])))
    return _no_store(data)


@router.post("/invitations/checkins/sync")
@limiter.limit("30/minute", key_func=user_key)
def offline_sync(request: Request, data: SyncIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if normalize_role(user.role) not in ("agent", "organizer", "admin", "staff"):
        raise HTTPException(403, "Accès refusé")
    return {"results": inv.sync_checkins(db, user, data.device_id, [i.model_dump() for i in data.items], request)}
