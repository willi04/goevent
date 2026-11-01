"""
api/quotes.py — Équipe commerciale (lot 4.5) : devis numérotés et commissions.

Devis (admin + commercial ; un commercial ne voit que les siens, 404 sinon) :
  GET    /admin/invitations/quotes                    liste, filtres, recherche
  POST   /admin/invitations/quotes/price              récapitulatif en direct (aucune écriture)
  POST   /admin/invitations/quotes                    création (numéro DEV-AAAA-NNNN, contrôle de conflit)
  GET    /admin/invitations/quotes/{id}               détail
  PATCH  /admin/invitations/quotes/{id}               modification d'un brouillon (numéro conservé)
  POST   /admin/invitations/quotes/{id}/sent|accepted|refused|duplicate
  POST   /admin/invitations/quotes/{id}/validate      admin : valide un brouillon « à valider »
  POST   /admin/invitations/quotes/{id}/convert       admin : crée l'événement pré-rempli
  POST   /admin/invitations/quotes/{id}/owner         admin : réaffecte
  GET    /admin/invitations/quotes/{id}/pdf           PDF A4 (journalisé, jamais public)
  GET    /admin/invitations/quotes/{id}/whatsapp      texte à copier
Commissions (commercial : les siennes ; admin : tout) :
  GET    /admin/sales/sellers                         admin : commerciaux (filtres, réaffectation)
  GET    /admin/sales/commissions?period=AAAA-MM      relevé du mois (mois de l'événement)
  POST   /admin/sales/commissions/close               admin : clôture du mois
  POST   /admin/sales/commissions/pay                 admin : versement (date + référence)
  GET    /admin/sales/commissions/statement.csv       relevé CSV
"""
import csv
import io
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core import invitation_catalog as cat
from core import invitations as inv
from core import quotes as qt
from core import sales
from core.database import get_db
from core.limiter import limiter, user_key
from core.permissions import get_current_admin, is_admin, require_staff_function, sees_all_sales
from core.security import log_security_event
from models.commission import Commission
from models.event import Event
from models.invitation import InvitationPack, PrivateEventSettings
from models.invitation_quote import InvitationQuote
from models.user import User
from schemas.invitation_catalog import QuoteIn
from schemas.quotes import (
    CommissionPayIn, QuoteConvertIn, QuoteCreateIn, QuoteOwnerIn, QuotePatchIn, QuoteRefuseIn,
)

router = APIRouter(tags=["quotes"])
seller_or_admin = require_staff_function("commercial")


# ── helpers ──────────────────────────────────────────────────────────
def _phone(v: Optional[str]) -> Optional[str]:
    try:
        return inv.normalize_phone(v)
    except ValueError as e:
        raise HTTPException(400, "Téléphone : %s." % e)


def _owner(db: Session, user: User, requested: Optional[int]) -> User:
    if not sees_all_sales(user):
        return user
    if not requested or requested == user.id:
        return user
    o = db.query(User).filter(User.id == requested, User.is_active == True).first()  # noqa: E712
    if not o or not sales.is_seller(o):
        raise HTTPException(400, "Propriétaire invalide : choisissez un commercial actif.")
    return o


def _price(db: Session, user: User, order: QuoteIn) -> dict:
    admin = is_admin(user)
    data = order.model_dump()
    q = cat.price(db, admin, data, draft=not admin)
    q["input"] = data              # pour « Dupliquer » (recalcul au prix du jour)
    q["catalog_at"] = cat.utcnow().isoformat()
    return q


def _check_conflicts(db: Session, user: User, owner: User, phone, name_n, company_n, event_date,
                     force_reason: Optional[str], exclude_id: Optional[int] = None, request: Optional[Request] = None,
                     label: str = ""):
    blocking, dup = qt.find_conflicts(db, owner.id, phone, name_n, company_n, event_date, exclude_id,
                                      owner_is_admin=is_admin(owner))
    forced = None
    if blocking:
        if not is_admin(user):
            log_security_event(db, "invitation_quote_conflict", request, user.id,
                               "%s : téléphone déjà suivi (%s %s)" % (label, blocking["kind"], blocking["id"]))
            raise HTTPException(409, "Prospect déjà suivi par un autre commercial : l'administrateur tranchera. "
                                     "Le devis n'a pas été enregistré.")
        reason = " ".join((force_reason or "").split())
        if len(reason) < 3:
            who = db.get(User, blocking["owner_id"]) if blocking.get("owner_id") else None
            what = ("devis %s" % blocking["number"]) if blocking["kind"] == "quote" else ("événement « %s »" % blocking["title"])
            raise HTTPException(409, "Conflit : ce numéro est déjà suivi (%s, %s). Indiquez un motif pour passer outre."
                                % (what, who.full_name if who else "administration"))
        forced = blocking.get("id") if blocking["kind"] == "quote" else None
        log_security_event(db, "invitation_quote_conflict_forced", request, user.id,
                           "%s : %s %s — %s" % (label, blocking["kind"], blocking["id"], reason[:200]))
    return forced, dup


def _out(db: Session, user: User, q: InvitationQuote, detail: bool = True) -> dict:
    return qt.quote_out(q, sees_all_sales(user), qt.owners_map(db, [q.owner_id]), detail=detail)


def _apply_client(q: InvitationQuote, data) -> None:
    q.client_name = " ".join(data.client_name.split())
    q.client_company = " ".join((data.client_company or "").split()) or None
    q.client_phone = _phone(data.client_phone)
    q.event_type = " ".join((data.event_type or "").split()) or None
    q.event_date = data.event_date
    q.location = " ".join((data.location or "").split()) or None
    q.guests_count = data.guests_count
    q.phone_norm = q.client_phone
    q.name_norm = qt.norm_name(q.client_name)
    q.company_norm = qt.norm_name(q.client_company)


def _apply_price(q: InvitationQuote, p: dict) -> None:
    q.snapshot = p
    q.formula_id = p["formula"]["id"]
    q.total_fcfa = int(p["amount_due"])
    q.pack_amount_fcfa = sales.commission_base(p)
    if p.get("needs_admin"):
        q.needs_admin, q.validated_by, q.validated_at = True, None, None
    else:
        q.needs_admin = False


def _sendable(q: InvitationQuote) -> None:
    if q.purged_at:
        raise HTTPException(400, "Devis anonymisé (conservation dépassée).")
    if q.needs_admin and not q.validated_at:
        raise HTTPException(400, "Devis à valider par l'administrateur (sur mesure ou remise manuelle) avant tout envoi.")


# ════════════════════════════════════════════════════════════
# DEVIS
# ════════════════════════════════════════════════════════════
@router.get("/admin/invitations/quotes")
@limiter.limit("120/minute", key_func=user_key)
def list_quotes(request: Request, status: Optional[str] = Query(None, max_length=12),
                owner_id: Optional[int] = None, flag: Optional[str] = Query(None, max_length=12),
                q: Optional[str] = Query(None, max_length=60), event_id: Optional[int] = None, page: int = Query(1, ge=1),
                limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db),
                user: User = Depends(seller_or_admin)):
    today = sales.today_bangui()
    query = db.query(InvitationQuote)
    if not sees_all_sales(user):
        query = query.filter(InvitationQuote.owner_id == user.id)
    elif owner_id:
        query = query.filter(InvitationQuote.owner_id == owner_id)
    if event_id:
        query = query.filter(InvitationQuote.event_id == event_id)
    if status == "expire":
        query = query.filter(InvitationQuote.status.in_(("brouillon", "envoye")), InvitationQuote.valid_until < today)
    elif status in ("brouillon", "envoye"):
        query = query.filter(InvitationQuote.status == status, InvitationQuote.valid_until >= today)
    elif status in qt.STATUSES:
        query = query.filter(InvitationQuote.status == status)
    if flag == "to_validate":
        query = query.filter(InvitationQuote.needs_admin == True, InvitationQuote.validated_at.is_(None))  # noqa: E712
    elif flag == "to_cash":
        query = query.filter(InvitationQuote.status == "accepte", InvitationQuote.pack_id.is_(None))
    elif flag == "duplicate" and sees_all_sales(user):
        query = query.filter(or_(InvitationQuote.possible_duplicate_of.isnot(None),
                                 InvitationQuote.conflict_with_quote_id.isnot(None)))
    if q and q.strip():
        term = q.strip()
        like = "%%%s%%" % term
        digits = "".join(c for c in term if c.isdigit())
        conds = [InvitationQuote.number.ilike(like), InvitationQuote.client_name.ilike(like),
                 InvitationQuote.client_company.ilike(like)]
        if len(digits) >= 4:
            conds.append(InvitationQuote.phone_norm.like("%%%s%%" % digits))
        query = query.filter(or_(*conds))
    total = query.count()
    rows = query.order_by(InvitationQuote.id.desc()).offset((page - 1) * limit).limit(limit).all()
    owners = qt.owners_map(db, [r.owner_id for r in rows])
    out = {"total": total, "page": page, "limit": limit,
           "quotes": [qt.quote_out(r, sees_all_sales(user), owners) for r in rows]}
    if sees_all_sales(user):
        base = db.query(InvitationQuote)
        out["counts"] = {
            "to_validate": base.filter(InvitationQuote.needs_admin == True, InvitationQuote.validated_at.is_(None)).count(),  # noqa: E712
            "to_cash": base.filter(InvitationQuote.status == "accepte", InvitationQuote.pack_id.is_(None)).count(),
            "duplicate": base.filter(or_(InvitationQuote.possible_duplicate_of.isnot(None),
                                         InvitationQuote.conflict_with_quote_id.isnot(None)),
                                     InvitationQuote.status.in_(qt.PROTECTED)).count(),
        }
    return out


@router.post("/admin/invitations/quotes/price")
@limiter.limit("120/minute", key_func=user_key)
def price_quote(request: Request, order: QuoteIn, db: Session = Depends(get_db), user: User = Depends(seller_or_admin)):
    p = _price(db, user, order)
    p.pop("input", None)
    return p


@router.post("/admin/invitations/quotes")
@limiter.limit("30/minute", key_func=user_key)
def create_quote(request: Request, data: QuoteCreateIn, db: Session = Depends(get_db),
                 user: User = Depends(seller_or_admin)):
    from api.invitations import _load
    owner = _owner(db, user, data.owner_id)
    event_id = None
    if data.event_id:
        ev, st = _load(db, data.event_id, user)
        event_id = ev.id
        if st.owner_id and st.owner_id != owner.id:
            owner = db.get(User, st.owner_id)           # devis d'un événement = propriétaire de l'événement
    p = _price(db, user, data.order)
    q = InvitationQuote(owner_id=owner.id, sales_code=owner.sales_code, status="brouillon", created_by=user.id,
                        event_id=event_id, number="", year=0, seq=0, snapshot={}, valid_until=sales.today_bangui(),
                        purge_after=sales.today_bangui())
    _apply_client(q, data)
    if not event_id:
        q.conflict_with_quote_id, q.possible_duplicate_of = _check_conflicts(
            db, user, owner, q.phone_norm, q.name_norm, q.company_norm, q.event_date, data.force_reason,
            request=request, label="nouveau devis")
    _apply_price(q, p)
    today = sales.today_bangui()
    q.valid_until = today + timedelta(days=qt.VALIDITY_DAYS)
    q.purge_after = qt.add_months(q.valid_until, qt.RETENTION_MONTHS)
    q.seq, q.number = qt.next_number(db, today.year)    # même transaction que l'insertion : ni trou ni doublon
    q.year = today.year
    db.add(q)
    db.commit()
    db.refresh(q)
    log_security_event(db, "invitation_quote_created", request, user.id, "%s · %s · %d FCFA%s%s" % (
        q.number, q.client_name[:40], q.total_fcfa, " · propriétaire #%d" % owner.id if owner.id != user.id else "",
        " · à valider" if q.needs_admin else ""))
    return _out(db, user, q)


@router.get("/admin/invitations/quotes/{quote_id}")
def get_quote(quote_id: int, db: Session = Depends(get_db), user: User = Depends(seller_or_admin)):
    return _out(db, user, qt.get_quote(db, quote_id, user))


@router.patch("/admin/invitations/quotes/{quote_id}")
@limiter.limit("60/minute", key_func=user_key)
def update_quote(request: Request, quote_id: int, data: QuotePatchIn, db: Session = Depends(get_db),
                 user: User = Depends(seller_or_admin)):
    q = qt.get_quote(db, quote_id, user, lock=True)
    if q.status != "brouillon" or qt.effective_status(q) == "expire":
        raise HTTPException(400, "Seul un brouillon valide se modifie : dupliquez ce devis pour en faire un nouveau.")
    owner = db.get(User, q.owner_id)
    _apply_client(q, data)
    if not q.event_id:
        q.conflict_with_quote_id, q.possible_duplicate_of = _check_conflicts(
            db, user, owner, q.phone_norm, q.name_norm, q.company_norm, q.event_date, data.force_reason,
            exclude_id=q.id, request=request, label=q.number)
    _apply_price(q, _price(db, user, data.order))
    q.updated_at = datetime.utcnow()
    db.commit()
    log_security_event(db, "invitation_quote_updated", request, user.id, "%s · %d FCFA" % (q.number, q.total_fcfa))
    return _out(db, user, q)


def _transition(db: Session, request: Request, user: User, quote_id: int, to: str, reason: Optional[str] = None):
    q = qt.get_quote(db, quote_id, user, lock=True)
    st = qt.effective_status(q)
    now = datetime.utcnow()
    if to == "envoye":
        if st != "brouillon":
            raise HTTPException(400, "Seul un brouillon valide peut être marqué envoyé (statut : %s)." % qt.STATUS_LABELS[st])
        _sendable(q)
        if not q.event_id:     # le conflit est revérifié au moment d'envoyer
            owner = db.get(User, q.owner_id)
            blocking, _ = qt.find_conflicts(db, owner.id, q.phone_norm, None, None, None, q.id, owner_is_admin=is_admin(owner))
            if blocking and not q.conflict_with_quote_id:
                if not is_admin(user):
                    raise HTTPException(409, "Prospect déjà suivi par un autre commercial : l'administrateur tranchera.")
        q.status, q.sent_at = "envoye", now
    elif to == "accepte":
        if st not in ("envoye", "brouillon"):
            raise HTTPException(400, "Devis non acceptable (statut : %s)." % qt.STATUS_LABELS[st])
        _sendable(q)
        q.status, q.accepted_at = "accepte", now
        q.sent_at = q.sent_at or now
    elif to == "refuse":
        if q.status not in qt.PROTECTED:
            raise HTTPException(400, "Devis déjà clôturé (statut : %s)." % qt.STATUS_LABELS[st])
        q.status, q.refused_at, q.refused_reason = "refuse", now, (reason or "").strip()[:300]
    q.updated_at = now
    db.commit()
    log_security_event(db, "invitation_quote_" + {"envoye": "sent", "accepte": "accepted", "refuse": "refused"}[to],
                       request, user.id, q.number + (" : " + q.refused_reason[:150] if to == "refuse" else ""))
    return _out(db, user, q)


@router.post("/admin/invitations/quotes/{quote_id}/sent")
@limiter.limit("60/minute", key_func=user_key)
def quote_sent(request: Request, quote_id: int, db: Session = Depends(get_db), user: User = Depends(seller_or_admin)):
    return _transition(db, request, user, quote_id, "envoye")


@router.post("/admin/invitations/quotes/{quote_id}/accepted")
@limiter.limit("60/minute", key_func=user_key)
def quote_accepted(request: Request, quote_id: int, db: Session = Depends(get_db), user: User = Depends(seller_or_admin)):
    return _transition(db, request, user, quote_id, "accepte")


@router.post("/admin/invitations/quotes/{quote_id}/refused")
@limiter.limit("60/minute", key_func=user_key)
def quote_refused(request: Request, quote_id: int, data: QuoteRefuseIn, db: Session = Depends(get_db),
                  user: User = Depends(seller_or_admin)):
    return _transition(db, request, user, quote_id, "refuse", data.reason)


@router.post("/admin/invitations/quotes/{quote_id}/validate")
@limiter.limit("60/minute")
def quote_validate(request: Request, quote_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    q = qt.get_quote(db, quote_id, user, lock=True)
    if not q.needs_admin or q.validated_at:
        raise HTTPException(400, "Ce devis n'attend pas de validation.")
    if qt.effective_status(q) != "brouillon":
        raise HTTPException(400, "Seul un brouillon valide peut être validé.")
    q.validated_by, q.validated_at, q.updated_at = user.id, datetime.utcnow(), datetime.utcnow()
    db.commit()
    log_security_event(db, "invitation_quote_validated", request, user.id, "%s · %d FCFA" % (q.number, q.total_fcfa))
    return _out(db, user, q)


@router.post("/admin/invitations/quotes/{quote_id}/duplicate")
@limiter.limit("30/minute", key_func=user_key)
def quote_duplicate(request: Request, quote_id: int, db: Session = Depends(get_db), user: User = Depends(seller_or_admin)):
    src = qt.get_quote(db, quote_id, user)
    if src.purged_at:
        raise HTTPException(400, "Devis anonymisé : impossible de le dupliquer.")
    order = (src.snapshot or {}).get("input")
    if not order:
        raise HTTPException(400, "Devis sans détail de saisie : créez-en un nouveau.")
    data = QuoteCreateIn(client_name=src.client_name, client_company=src.client_company, client_phone=src.client_phone,
                         event_type=src.event_type, event_date=src.event_date, location=src.location,
                         guests_count=src.guests_count, order=QuoteIn(**order), event_id=src.event_id,
                         owner_id=src.owner_id if sees_all_sales(user) else None,
                         force_reason=("Duplication de %s" % src.number) if is_admin(user) else None)
    return create_quote(request, data, db, user)


@router.post("/admin/invitations/quotes/{quote_id}/owner")
@limiter.limit("30/minute")
def quote_owner(request: Request, quote_id: int, data: QuoteOwnerIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_admin)):
    q = qt.get_quote(db, quote_id, user, lock=True)
    if q.status == "converti":
        raise HTTPException(400, "Devis converti : réaffectez l'événement.")
    new = _owner(db, user, data.owner_id)
    old = q.owner_id
    q.owner_id, q.sales_code, q.updated_at = new.id, new.sales_code, datetime.utcnow()
    db.commit()
    log_security_event(db, "invitation_quote_owner_changed", request, user.id, "%s : #%d → #%d" % (q.number, old, new.id))
    return _out(db, user, q)


@router.get("/admin/invitations/quotes/{quote_id}/pdf")
@limiter.limit("30/minute", key_func=user_key)
def quote_pdf(request: Request, quote_id: int, db: Session = Depends(get_db), user: User = Depends(seller_or_admin)):
    q = qt.get_quote(db, quote_id, user)
    _sendable(q)
    from core.sales_docs import quote_pdf as render
    pdf = render(q, db.get(User, q.owner_id))
    log_security_event(db, "invitation_quote_pdf", request, user.id, q.number)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="%s.pdf"' % q.number, "Cache-Control": "no-store"})


@router.get("/admin/invitations/quotes/{quote_id}/whatsapp")
def quote_whatsapp(quote_id: int, db: Session = Depends(get_db), user: User = Depends(seller_or_admin)):
    q = qt.get_quote(db, quote_id, user)
    _sendable(q)
    text = qt.whatsapp_text(q)
    return {"text": text, "wa_url": inv.wa_url(q.client_phone, text) if q.client_phone else None}


@router.post("/admin/invitations/quotes/{quote_id}/convert")
@limiter.limit("20/minute")
def quote_convert(request: Request, quote_id: int, data: QuoteConvertIn, db: Session = Depends(get_db),
                  user: User = Depends(get_current_admin)):
    """Crée l'événement privé pré-rempli depuis le devis (propriétaire = commercial du devis).
    L'activation (paiement reçu) se fait ensuite avec quote_id : même montant que le devis."""
    from api.invitations import create_private_event
    q = qt.get_quote(db, quote_id, user, lock=True)
    if q.event_id:
        return {"event_id": q.event_id, "quote": _out(db, user, q)}
    st = qt.effective_status(q)
    if st not in ("envoye", "accepte"):
        raise HTTPException(400, "Devis à convertir : envoyé ou accepté, et valide (statut : %s)." % qt.STATUS_LABELS[st])
    _sendable(q)
    try:
        when = datetime.fromisoformat(data.event_date.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Date de l'événement invalide.")
    owner = db.get(User, q.owner_id)
    ev = create_private_event(
        db, user, request, title=data.title, event_date=when, location=data.location, description=None,
        client_name=q.client_name, client_phone=q.client_phone, host_label=data.host_label,
        service_level=((q.snapshot or {}).get("formula") or {}).get("code") or "accompagne", message_template=None,
        owner_id=owner.id if sales.is_seller(owner) else None, source_quote_id=q.id)
    q = qt.get_quote(db, quote_id, user, lock=True)
    q.event_id, q.updated_at = ev.id, datetime.utcnow()
    log_security_event(db, "invitation_quote_converted", request, user.id, "%s → événement #%d" % (q.number, ev.id))
    db.commit()
    return {"event_id": ev.id, "quote": _out(db, user, q)}


# ════════════════════════════════════════════════════════════
# COMMISSIONS
# ════════════════════════════════════════════════════════════
@router.get("/admin/sales/sellers")
def sellers(db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    rows = (db.query(User).filter(User.role == "staff", User.staff_function.in_(sales.SALES_FUNCTIONS))
            .order_by(User.is_active.desc(), User.full_name).all())
    return {"sellers": [{"id": u.id, "full_name": u.full_name, "sales_code": u.sales_code, "is_active": bool(u.is_active),
                         "commission_rate": u.commission_rate} for u in rows]}


def _commission_rows(db: Session, user: User, period: str, owner_id: Optional[int]):
    """Commissions dont l'événement a lieu dans le mois (heure de Bangui), quel que soit leur statut."""
    start, end = sales.month_bounds(period)
    q = (db.query(Commission, InvitationPack, Event, User)
         .join(InvitationPack, InvitationPack.id == Commission.source_id)
         .join(Event, Event.id == InvitationPack.event_id)
         .join(User, User.id == Commission.beneficiary_user_id)
         .filter(Commission.source_type == sales.COMMISSION_SOURCE,
                 or_(Commission.period == period,
                     (Commission.period.is_(None)) & (Event.event_date >= start) & (Event.event_date < end))))
    if not sees_all_sales(user):
        q = q.filter(Commission.beneficiary_user_id == user.id)
    elif owner_id:
        q = q.filter(Commission.beneficiary_user_id == owner_id)
    return q.order_by(User.full_name, Event.event_date).all()


def _status(c: Commission, ev: Event) -> str:
    if c.status == "pending":
        return "a_venir" if ev.event_date >= datetime.utcnow() else "a_cloturer"
    return {"approved": "due", "paid": "versee", "cancelled": "annulee"}.get(c.status, c.status)


@router.get("/admin/sales/commissions")
def commissions(period: str = Query(..., max_length=7), owner_id: Optional[int] = None, db: Session = Depends(get_db),
                user: User = Depends(seller_or_admin)):
    rows = _commission_rows(db, user, period, owner_id)
    quote_numbers = {q.id: q.number for q in db.query(InvitationQuote).filter(
        InvitationQuote.id.in_({p.quote_id for _, p, _, _ in rows if p.quote_id})).all()} if rows else {}
    lines, totals = [], {}
    for c, p, ev, u in rows:
        s = _status(c, ev)
        lines.append({"id": c.id, "owner_id": u.id, "owner_name": u.full_name, "sales_code": u.sales_code,
                      "pack_id": p.id, "event_id": ev.id, "event_title": ev.title, "event_date": ev.event_date.isoformat(),
                      "quote_number": quote_numbers.get(p.quote_id), "base_fcfa": c.base_amount_fcfa,
                      "rate_pct": round(c.rate_applied * 100, 2), "amount_fcfa": c.amount_fcfa, "status": s,
                      "paid_at": c.paid_at.isoformat() if c.paid_at else None, "paid_ref": c.paid_ref})
        t = totals.setdefault(u.id, {"owner_id": u.id, "owner_name": u.full_name, "sales_code": u.sales_code,
                                     "sales": 0, "due": 0, "paid": 0, "upcoming": 0, "to_close": 0})
        if s != "annulee":
            t["sales"] += 1
        key = {"due": "due", "versee": "paid", "a_venir": "upcoming", "a_cloturer": "to_close"}.get(s)
        if key:
            t[key] += c.amount_fcfa
    return {"period": period, "deadline": sales.payout_deadline(period), "lines": lines,
            "totals": list(totals.values()), "can_manage": sees_all_sales(user)}


@router.post("/admin/sales/commissions/close")
@limiter.limit("10/minute")
def close_month(request: Request, period: str = Query(..., max_length=7), db: Session = Depends(get_db),
                user: User = Depends(get_current_admin)):
    """Clôture : commissions « pending » dont l'événement (pack actif) a eu lieu dans le mois → dues."""
    start, end = sales.month_bounds(period)
    now = datetime.utcnow()
    rows = (db.query(Commission).join(InvitationPack, InvitationPack.id == Commission.source_id)
            .join(Event, Event.id == InvitationPack.event_id)
            .filter(Commission.source_type == sales.COMMISSION_SOURCE, Commission.status == "pending",
                    InvitationPack.status == "actif", Event.event_date >= start, Event.event_date < end,
                    Event.event_date < now)
            .with_for_update(of=Commission).all())
    for c in rows:
        c.status, c.period, c.approved_at, c.approved_by = "approved", period, now, user.id
    db.commit()
    total = sum(c.amount_fcfa for c in rows)
    log_security_event(db, "commission_month_closed", request, user.id, "%s : %d commission(s), %d FCFA" % (period, len(rows), total))
    return {"closed": len(rows), "total_fcfa": total}


@router.post("/admin/sales/commissions/pay")
@limiter.limit("20/minute")
def pay_commissions(request: Request, data: CommissionPayIn, period: str = Query(..., max_length=7),
                    owner_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_admin)):
    sales.month_bounds(period)
    rows = (db.query(Commission).filter(Commission.source_type == sales.COMMISSION_SOURCE,
                                        Commission.beneficiary_user_id == owner_id, Commission.period == period,
                                        Commission.status == "approved").with_for_update().all())
    if not rows:
        raise HTTPException(400, "Aucune commission due pour ce commercial et ce mois (clôturez le mois d'abord).")
    paid_at = datetime(data.paid_on.year, data.paid_on.month, data.paid_on.day, 12)
    ref = " ".join(data.reference.split())[:80]
    for c in rows:
        c.status, c.paid_at, c.paid_by, c.paid_ref = "paid", paid_at, user.id, ref
    db.commit()
    total = sum(c.amount_fcfa for c in rows)
    log_security_event(db, "commission_paid", request, user.id,
                       "%s · commercial #%d · %d commission(s) · %d FCFA · réf. %s" % (period, owner_id, len(rows), total, ref))
    return {"paid": len(rows), "total_fcfa": total}


@router.get("/admin/sales/commissions/statement.csv")
def statement_csv(period: str = Query(..., max_length=7), owner_id: Optional[int] = None,
                  db: Session = Depends(get_db), user: User = Depends(seller_or_admin)):
    data = commissions(period, owner_id, db, user)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Mois", "Commercial", "Code", "Événement", "Date", "Devis", "Base (FCFA)", "Taux (%)",
                "Commission (FCFA)", "Statut", "Versée le", "Référence"])
    labels = {"a_venir": "À venir", "a_cloturer": "À clôturer", "due": "Due", "versee": "Versée", "annulee": "Annulée"}
    for ln in data["lines"]:
        w.writerow([period, ln["owner_name"], ln["sales_code"] or "", ln["event_title"], ln["event_date"][:10],
                    ln["quote_number"] or "", ln["base_fcfa"], str(ln["rate_pct"]).replace(".", ","), ln["amount_fcfa"],
                    labels.get(ln["status"], ln["status"]), (ln["paid_at"] or "")[:10], ln["paid_ref"] or ""])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="commissions-%s.csv"' % period, "Cache-Control": "no-store"})
