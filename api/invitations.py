"""
api/invitations.py — Invitations digitales à QR code (événements privés).

Back-office (offre accompagnée) — capability invitations_manage (admin + commercial) :
  /admin/invitations/*            événements privés, invités, envoi WhatsApp, suivi, agents
  activation / annulation de pack → admin uniquement (invitations_activate)
Public (sans compte, rate limit) :
  GET /invitations/pass/{token}               pass nominatif (jamais de téléphone)
  GET /invitations/pass/{token}/calendar.ics  ajout au calendrier
  GET /invitations/track/{token}              lien de suivi client : chiffres uniquement
Scanner (agent assigné / orga propriétaire) :
  GET  /invitations/offline/{event_id}        liste hors ligne (empreintes des jetons)
  POST /invitations/checkins/sync             entrées faites hors ligne
  (le scan en ligne passe par POST /scan, cf. api/tickets.py)
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core import invitation_catalog as cat
from core import invitations as inv
from core.database import get_db
from core.limiter import PUBLIC_IP_CEILING, limiter, path_token_key, user_key
from core.permissions import get_current_admin, is_admin, normalize_role, require_staff_function
from core.security import get_current_user, hash_pin, log_security_event
from models.event import Event
from models.invitation import EventGuest, GuestCheckin, InvitationPack, PrivateEventSettings
from models.invitation_catalog import InvFormula
from models.user import User
from schemas.invitation import (
    AgentIn, AgentManualIn, GuestImportIn, GuestPatch, ManualCheckinIn, PackCancelIn, PrivateEventIn,
    PrivateEventPatch, SyncIn,
)
from schemas.invitation_catalog import OrderIn

router = APIRouter(tags=["invitations"])
manager = require_staff_function("commercial")   # admin + commercial (pas le CM)


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


def _guest_out(ev: Event, st: PrivateEventSettings, g: EventGuest) -> dict:
    msg = inv.render_message(ev, st, g) if g.token else ""
    return {"id": g.id, "full_name": g.full_name, "phone": g.phone, "seats": g.seats, "seats_used": g.seats_used,
            "table_label": g.table_label, "category": g.category, "status": _guest_status(g),
            "sent_at": _iso(g.sent_at), "first_checkin_at": _iso(g.first_checkin_at),
            "last_checkin_at": _iso(g.last_checkin_at), "revoked_at": _iso(g.revoked_at),
            "pass_url": inv.pass_url(g.token) if g.token else None, "paper": g.paper,
            "wa_url": inv.wa_url(g.phone, msg) if g.token and not g.paper else None, "message": msg}


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
            "vat": snap.get("vat"), "amount_ht": p.amount_ht, "amount_vat": p.amount_vat}


def _event_summary(db: Session, ev: Event, st: PrivateEventSettings) -> dict:
    return {"id": ev.id, "title": ev.title, "event_date": _iso(ev.event_date),
            "event_date_label": inv.fmt_date_fr(ev.event_date), "location": ev.location,
            "client_name": st.client_name, "service_level": st.service_level,
            "formula_name": cat.formula_label(db, st.service_level),
            "quota": inv.quota(db, ev.id), "listed": inv.seats_listed(db, ev.id),
            "features": cat.event_features(db, ev.id, st.service_level), "stats": inv.stats(db, ev.id),
            "purged": bool(st.purged_at)}


def _load(db: Session, event_id: int, lock: bool = False):
    ev = inv.get_private_event(db, event_id, lock=lock)
    return ev, inv.get_settings(db, ev.id)


def _guest(db: Session, guest_id: int, lock: bool = False):
    q = db.query(EventGuest).filter(EventGuest.id == guest_id)
    if lock:
        q = q.with_for_update()
    g = q.first()
    if not g:
        raise HTTPException(404, "Invité introuvable")
    ev, st = _load(db, g.event_id)
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


def _phone(v: Optional[str]) -> Optional[str]:
    try:
        return inv.normalize_phone(v)
    except ValueError as e:
        raise HTTPException(400, "Téléphone : %s." % e)


# ════════════════════════════════════════════════════════════
# BACK-OFFICE — événements privés
# ════════════════════════════════════════════════════════════

@router.get("/admin/invitations/meta")
def admin_meta(db: Session = Depends(get_db), user: User = Depends(manager)):
    c = cat.full_catalog(db, include_archived=False)
    return {"formulas": c["formulas"], "packs": [p for p in c["packs"] if p["available"]], "options": c["options"],
            "catalog_settings": c["settings"], "units": c["units"],
            "channels": inv.channels(), "default_message": inv.DEFAULT_MESSAGE,
            "message_vars": list(inv.MESSAGE_VARS), "can_activate": is_admin(user), "can_edit_catalog": is_admin(user),
            "retention_days": inv.INVITATION_RETENTION_DAYS}


@router.get("/admin/invitations/events")
def admin_list_events(db: Session = Depends(get_db), user: User = Depends(manager)):
    rows = (db.query(Event, PrivateEventSettings)
            .join(PrivateEventSettings, PrivateEventSettings.event_id == Event.id)
            .filter(Event.visibility == "private", Event.is_active == True)  # noqa: E712
            .order_by(Event.event_date.desc()).limit(300).all())
    return {"events": [_event_summary(db, ev, st) for ev, st in rows], "summary": cat.revenue_summary(db)}


@router.post("/admin/invitations/events")
@limiter.limit("30/minute")
def admin_create_event(request: Request, data: PrivateEventIn, db: Session = Depends(get_db),
                       user: User = Depends(manager)):
    date = inv.naive_utc(data.event_date)
    if date < inv.utcnow() - timedelta(days=1):
        raise HTTPException(400, "La date de l'événement est déjà passée.")
    house = inv.get_house_organizer(db)
    ev = Event(title=data.title.strip(), description=(data.description or "").strip(), location=data.location.strip(),
               category="Privé", event_date=date, price=0, total_seats=0, seats_sold=0, ticket_tiers=[],
               is_active=True, visibility="private", organizer_id=house.id)
    db.add(ev)
    db.flush()
    st = PrivateEventSettings(event_id=ev.id, client_name=data.client_name.strip(), client_phone=_phone(data.client_phone),
                              host_label=(data.host_label or "").strip() or None,
                              message_template=inv.check_template(data.message_template),
                              service_level=_service(db, data.service_level), purge_after=inv.purge_after_for(date),
                              created_by=user.id)
    db.add(st)
    db.commit()
    log_security_event(db, "invitation_event_created", request, user.id, "event %d « %s » (%s)" % (ev.id, ev.title[:60], st.client_name[:40]))
    return admin_get_event(ev.id, db, user)


@router.get("/admin/invitations/events/{event_id}")
def admin_get_event(event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id)
    packs = db.query(InvitationPack).filter(InvitationPack.event_id == ev.id).order_by(InvitationPack.id).all()
    agents = db.query(User).filter(User.role == "agent", User.agent_event_id == ev.id, User.is_active == True).all()  # noqa: E712
    out = _event_summary(db, ev, st)
    out.update({"description": ev.description, "client_phone": st.client_phone, "host_label": st.host_label,
                "message_template": st.message_template, "message_is_default": st.message_template is None,
                "track_url": inv.track_url(st.track_token) if st.track_token else None,
                "purge_after": _iso(st.purge_after), "packs": [_pack_out(p) for p in packs],
                "agents": [{"id": a.id, "full_name": a.full_name, "phone_number": a.phone_number} for a in agents],
                "agents_included": cat.agents_included_total(db, ev.id)})
    return out


@router.patch("/admin/invitations/events/{event_id}")
@limiter.limit("60/minute")
def admin_update_event(request: Request, event_id: int, data: PrivateEventPatch, db: Session = Depends(get_db),
                       user: User = Depends(manager)):
    ev, st = _load(db, event_id)
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
            st.purge_after = inv.purge_after_for(ev.event_date)
    if "client_name" in f and data.client_name:
        st.client_name = data.client_name.strip()
    if "client_phone" in f:
        st.client_phone = _phone(data.client_phone)
    if "host_label" in f:
        st.host_label = (data.host_label or "").strip() or None
    if "service_level" in f and data.service_level:
        st.service_level = _service(db, data.service_level, st.service_level)
    if data.reset_message:
        st.message_template = None
    elif "message_template" in f:
        st.message_template = inv.check_template(data.message_template)
    db.commit()
    log_security_event(db, "invitation_event_updated", request, user.id, "event %d" % ev.id)
    return admin_get_event(ev.id, db, user)


# ── invités ──────────────────────────────────────────────────────────
@router.get("/admin/invitations/events/{event_id}/guests")
def admin_guests(event_id: int, q: Optional[str] = Query(None, max_length=80),
                 status: Optional[str] = Query(None, max_length=20), page: int = Query(1, ge=1),
                 limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id)
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
    return {"total": total, "page": page, "limit": limit, "guests": [_guest_out(ev, st, g) for g in rows],
            "send_queue": pending_ids,
            # Pass jamais envoyés faute de numéro : à compléter ou à transmettre à la main (« Copier le lien »)
            "pending_without_phone": db.query(EventGuest.id).filter(
                EventGuest.event_id == ev.id, EventGuest.sent_at.is_(None), EventGuest.revoked_at.is_(None),
                EventGuest.phone.is_(None), EventGuest.token.isnot(None), EventGuest.paper == False).count(),  # noqa: E712
            "paper_pending": paper_pending, "paper_total": paper_total}


@router.get("/admin/invitations/guests/{guest_id}")
def admin_guest(guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id)
    return _guest_out(ev, st, g)


@router.post("/admin/invitations/events/{event_id}/guests/import")
@limiter.limit("30/minute")
def admin_import(request: Request, event_id: int, data: GuestImportIn, db: Session = Depends(get_db),
                 user: User = Depends(manager)):
    ev, st = _load(db, event_id, lock=not data.dry_run)   # verrou : deux imports simultanés ne dépassent pas le quota
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée (conservation de 30 jours dépassée).")
    return inv.import_guests(db, ev, [r.model_dump() for r in data.rows], user, data.dry_run, request)


@router.post("/admin/invitations/events/{event_id}/guests/read-file")
@limiter.limit("30/minute")
async def admin_read_file(request: Request, event_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                          user: User = Depends(manager)):
    """Lit un .xlsx / .docx et renvoie son contenu tabulé (rien n'est importé ni stocké)."""
    inv.get_private_event(db, event_id)
    data = await file.read(inv.MAX_FILE_BYTES + 1)
    return inv.file_to_text(file.filename, data)


@router.patch("/admin/invitations/guests/{guest_id}")
@limiter.limit("120/minute")
def admin_update_guest(request: Request, guest_id: int, data: GuestPatch, db: Session = Depends(get_db),
                       user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, lock=True)
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
    return _guest_out(ev, st, g)


@router.post("/admin/invitations/guests/{guest_id}/revoke")
@limiter.limit("120/minute")
def admin_revoke(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, lock=True)
    if not g.revoked_at:
        g.revoked_at = inv.utcnow()
        db.commit()
        log_security_event(db, "invitation_pass_revoked", request, user.id, "pass %d (event %d)" % (g.id, ev.id))
    return _guest_out(ev, st, g)


@router.post("/admin/invitations/guests/{guest_id}/restore")
@limiter.limit("120/minute")
def admin_restore(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, lock=True)
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
    return _guest_out(ev, st, g)


@router.post("/admin/invitations/guests/{guest_id}/regenerate")
@limiter.limit("60/minute")
def admin_regenerate(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, lock=True)
    tok = inv.new_token()
    g.token, g.token_hash, g.sent_at, g.sent_by = tok, inv.token_hash(tok), None, None
    db.commit()
    log_security_event(db, "invitation_pass_regenerated", request, user.id, "pass %d (event %d)" % (g.id, ev.id))
    return _guest_out(ev, st, g)


@router.post("/admin/invitations/guests/{guest_id}/sent")
@limiter.limit("600/minute")
def admin_mark_sent(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, lock=True)
    inv.require_active(db, ev.id)
    if g.revoked_at:
        raise HTTPException(400, "Pass annulé.")
    g.sent_at, g.sent_by = inv.utcnow(), user.id
    db.commit()
    return _guest_out(ev, st, g)


@router.delete("/admin/invitations/guests/{guest_id}")
@limiter.limit("120/minute")
def admin_delete_guest(request: Request, guest_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id, lock=True)
    if g.seats_used > 0:
        raise HTTPException(400, "Cet invité est déjà entré : annulez le pass plutôt que de le supprimer.")
    db.query(GuestCheckin).filter(GuestCheckin.guest_id == g.id).delete(synchronize_session=False)
    db.delete(g)
    db.commit()
    log_security_event(db, "invitation_guest_deleted", request, user.id, "pass %d (event %d)" % (guest_id, ev.id))
    return {"deleted": guest_id}


@router.post("/admin/invitations/guests/{guest_id}/checkin")
@limiter.limit("120/minute", key_func=user_key)
def admin_manual_checkin(request: Request, guest_id: int, data: ManualCheckinIn, db: Session = Depends(get_db),
                         user: User = Depends(manager)):
    g, ev, st = _guest(db, guest_id)
    inv.require_active(db, ev.id)
    g = inv.manual_checkin(db, user, g, data.count, data.reason.strip(), request)
    return _guest_out(ev, st, g)


@router.get("/admin/invitations/events/{event_id}/paper.pdf")
@limiter.limit("20/minute")
def admin_paper_pdf(request: Request, event_id: int, scope: str = Query("paper", pattern="^(paper|all|pending)$"),
                    db: Session = Depends(get_db), user: User = Depends(manager)):
    """PDF A4 des pass à imprimer (8 par page, découpables). scope : paper (pass papier), pending
    (pass papier pas encore imprimés), all (tous les pass actifs). Les pass papier inclus sont marqués remis."""
    ev, st = _load(db, event_id)
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
    ev, st = _load(db, event_id)
    rows = (db.query(GuestCheckin, EventGuest.full_name, EventGuest.table_label)
            .join(EventGuest, EventGuest.id == GuestCheckin.guest_id)
            .filter(GuestCheckin.event_id == ev.id).order_by(GuestCheckin.scanned_at.desc()).limit(30).all())
    return {"stats": inv.stats(db, ev.id), "arrivals": [
        {"name": n, "table": t, "count": c.count, "source": c.source, "at": _iso(c.scanned_at), "conflict": c.conflict}
        for c, n, t in rows]}


# ── lien de suivi client ─────────────────────────────────────────────
@router.post("/admin/invitations/events/{event_id}/track-link")
@limiter.limit("30/minute")
def admin_track_link(request: Request, event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id)
    if not inv.quota(db, ev.id) or not cat.event_features(db, ev.id, st.service_level)["track_link"]:
        raise HTTPException(400, "Le lien de suivi n'est inclus dans aucun pack actif de cet événement.")
    st.track_token, st.track_created_at = secrets.token_urlsafe(24), inv.utcnow()
    db.commit()
    log_security_event(db, "invitation_track_link_created", request, user.id, "event %d" % ev.id)
    return {"track_url": inv.track_url(st.track_token)}


@router.delete("/admin/invitations/events/{event_id}/track-link")
@limiter.limit("30/minute")
def admin_track_revoke(request: Request, event_id: int, db: Session = Depends(get_db), user: User = Depends(manager)):
    ev, st = _load(db, event_id)
    st.track_token = None
    db.commit()
    log_security_event(db, "invitation_track_link_revoked", request, user.id, "event %d" % ev.id)
    return {"track_url": None}


# ── agents de contrôle ───────────────────────────────────────────────
@router.post("/admin/invitations/events/{event_id}/agents")
@limiter.limit("30/minute")
def admin_add_agent(request: Request, event_id: int, data: AgentIn, db: Session = Depends(get_db),
                    user: User = Depends(manager)):
    ev, st = _load(db, event_id)
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
    ev, st = _load(db, event_id)
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
    ev, st = _load(db, event_id, lock=True)
    if st.purged_at:
        raise HTTPException(400, "Liste supprimée.")
    p = cat.activate(db, ev, user, True, data.model_dump(), inv.channels(), request)
    return _pack_out(p)


@router.post("/admin/invitations/packs/{pack_id}/cancel")
@limiter.limit("20/minute")
def admin_cancel_pack(request: Request, pack_id: int, data: PackCancelIn, db: Session = Depends(get_db),
                      user: User = Depends(get_current_admin)):
    p = db.query(InvitationPack).filter(InvitationPack.id == pack_id).with_for_update().first()
    if not p or p.status != "actif":
        raise HTTPException(404, "Pack actif introuvable")
    p.status, p.cancelled_by, p.cancelled_at, p.cancel_reason = "annule", user.id, inv.utcnow(), data.reason.strip()
    db.commit()
    log_security_event(db, "invitation_pack_cancelled", request, user.id,
                       "pack %d (event %d) : %s" % (p.id, p.event_id, data.reason[:150]))
    return _pack_out(p)


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


@router.get("/invitations/pass/{token}")
@limiter.limit("30/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_pass(request: Request, token: str, db: Session = Depends(get_db)):
    g, ev, st = _pass_guest(db, token)
    if not ev.is_active:
        status = "cancelled"
    elif g.revoked_at:
        status = "revoked"
    elif inv.quota(db, ev.id) <= 0:
        status = "inactive"
    else:
        status = _guest_status(g) if g.seats_used else "valid"
    return _no_store({
        "status": status, "guest_name": g.full_name, "seats": g.seats, "seats_used": g.seats_used,
        "table": g.table_label, "category": g.category, "event_title": ev.title, "event_date": _iso(ev.event_date),
        "event_date_label": inv.fmt_date_fr(ev.event_date), "location": ev.location,
        "host_label": st.host_label or st.client_name, "pass_url": inv.pass_url(g.token),
    })


def _ics(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@router.get("/invitations/pass/{token}/calendar.ics")
@limiter.limit("30/minute", key_func=path_token_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def public_pass_ics(request: Request, token: str, db: Session = Depends(get_db)):
    g, ev, st = _pass_guest(db, token)
    start = ev.event_date or inv.utcnow()
    fmt = "%Y%m%dT%H%M%SZ"
    body = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GoEvent//Invitations//FR", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT", "UID:goevent-invitation-%d@goevent.africa" % g.id,
        "DTSTAMP:%s" % inv.utcnow().strftime(fmt), "DTSTART:%s" % start.strftime(fmt),
        "DTEND:%s" % (start + timedelta(hours=4)).strftime(fmt),
        "SUMMARY:%s" % _ics(ev.title), "LOCATION:%s" % _ics(ev.location or ""),
        "DESCRIPTION:%s" % _ics("Votre pass (%s) : %s" % (inv.places_label(g.seats), inv.pass_url(g.token))),
        "END:VEVENT", "END:VCALENDAR", ""])
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
    # Chiffres uniquement : ni lieu, ni nom, ni numéro d'invité.
    return JSONResponse({"event_title": ev.title, "event_date": _iso(ev.event_date),
                         "event_date_label": inv.fmt_date_fr(ev.event_date), "expected": s["expected"],
                         "entered": s["entered"], "remaining": s["remaining"], "rate": s["rate"],
                         "updated_at": inv.utcnow().isoformat() + "Z"},
                        headers={"Cache-Control": "public, max-age=15", "X-Robots-Tag": "noindex"})


# ════════════════════════════════════════════════════════════
# SCANNER — hors ligne
# ════════════════════════════════════════════════════════════

def _scanner_event(db: Session, event_id: int, user: User) -> Event:
    from api.tickets import _can_operate_event
    ev = inv.get_private_event(db, event_id)
    if not (_can_operate_event(user, ev) or normalize_role(user.role) == "admin"
            or (normalize_role(user.role) == "staff" and user.staff_function == "commercial")):
        raise HTTPException(403, "Accès réservé aux agents de cet événement.")
    return ev


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
