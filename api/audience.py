"""
Chantier 19.B : Audience — listing, segments, stats, export CSV.

Source de vérité = table tickets, calcul à la volée des segments.
Aucune nouvelle table de données métier (juste audience_export_logs pour traçage).

Sécurité :
  - Scoping strict : un orga ne voit QUE les acheteurs de SES events
  - Log audit obligatoire à chaque export CSV
  - Mention légale en ligne 1 du CSV
"""
import csv
import io
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from core.database import get_db
from core.security import get_current_user, log_security_event

from models.event import Event
from models.ticket import Ticket
from models.user import User
from models.audience_log import AudienceExportLog

from schemas.audience import (
    AudienceContactOut, AudienceListOut,
    AudienceStatsOut, SegmentStatsOut,
)

from utils.audience import (
    compute_segments, get_segment_stats,
    COMMERCIAL_STATUSES, ABANDONED_STATUSES, CONFIRMED_STATUSES,
)

router = APIRouter(tags=["audience"])


# ─── Helpers internes ────────────────────────────────────────────

def _assert_organizer(user: User) -> None:
    """Calque api/promotion.py — rôle legacy 'organisation' encore actif."""
    if user.role not in ("organizer", "organisation"):
        raise HTTPException(403, "Accès réservé aux organisateurs")


def _build_audience_query(
    db: Session,
    organizer_id: int,
    event_id: Optional[int],
    status: Optional[str],
    include_abandoned: bool,
    q: Optional[str],
):
    """
    Construit la query Ticket pour l'audience d'un orga.
    Retourne une query SQLAlchemy non exécutée (pour réutilisation count + list).
    """
    query = (
        db.query(Ticket)
        .options(joinedload(Ticket.owner), joinedload(Ticket.event))
        .join(Event, Event.id == Ticket.event_id)
        .filter(Event.organizer_id == organizer_id)
    )

    # Filtre event
    if event_id is not None:
        query = query.filter(Ticket.event_id == event_id)

    # Filtre statut
    if status:
        query = query.filter(Ticket.payment_status == status)
    elif include_abandoned:
        # Tous les statuts (commerciaux + abandonnés)
        all_statuses = list(COMMERCIAL_STATUSES) + list(ABANDONED_STATUSES)
        query = query.filter(Ticket.payment_status.in_(all_statuses))
    else:
        # Par défaut : statuts à valeur commerciale uniquement
        query = query.filter(Ticket.payment_status.in_(COMMERCIAL_STATUSES))

    # Recherche nom/téléphone insensible casse
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(
                func.lower(User.full_name).like(like),
                func.lower(User.phone_number).like(like),
                func.lower(User.email).like(like),
            )
        ).join(User, User.id == Ticket.user_id)

    return query


def _ticket_to_contact(
    ticket: Ticket,
    segments_map: dict,
) -> AudienceContactOut:
    """Sérialise un ticket en AudienceContactOut avec annotation segments."""
    user_segments = list(segments_map.get(ticket.user_id, set()))

    # Récupération du nom du tier depuis ticket_tiers de l'event si applicable
    categorie = None
    if ticket.event and ticket.event.ticket_tiers:
        tiers = ticket.event.ticket_tiers
        if isinstance(tiers, list):
            # Heuristique : on tente de retrouver le tier par prix (cash_amount)
            # Limite : si plusieurs tiers ont le même prix, on prend le premier.
            for t in tiers:
                if isinstance(t, dict):
                    try:
                        if float(t.get("price", 0)) == float(ticket.cash_amount or 0):
                            categorie = t.get("name")
                            break
                    except (ValueError, TypeError):
                        continue

    return AudienceContactOut(
        ticket_id=ticket.id,
        user_id=ticket.user_id,
        nom=ticket.owner.full_name if ticket.owner else "—",
        telephone=ticket.owner.phone_number if ticket.owner else None,
        email=ticket.owner.email if ticket.owner else None,
        event_id=ticket.event_id,
        event_titre=ticket.event.title if ticket.event else "Événement supprimé",
        event_date=ticket.event.event_date if ticket.event else None,
        categorie=categorie,
        prix_paye_fcfa=float(ticket.cash_amount or 0),
        statut=ticket.payment_status,
        methode_paiement=ticket.payment_method,
        date_achat=ticket.purchased_at,
        segments=user_segments,
    )


# ═════════════════════════════════════════════════════════════════
# ROUTES
# ═════════════════════════════════════════════════════════════════

@router.get("/organizer/audience/stats", response_model=AudienceStatsOut)
def audience_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stats globales du header de page audience."""
    _assert_organizer(current_user)

    # CA total cumulé (tickets confirmés uniquement)
    total_ca = (
        db.query(func.coalesce(func.sum(Ticket.cash_amount), 0))
        .join(Event, Event.id == Ticket.event_id)
        .filter(Event.organizer_id == current_user.id)
        .filter(Ticket.payment_status.in_(CONFIRMED_STATUSES))
        .scalar()
    ) or 0

    # Nombre de contacts uniques (tous statuts commerciaux)
    total_contacts = (
        db.query(func.count(func.distinct(Ticket.user_id)))
        .join(Event, Event.id == Ticket.event_id)
        .filter(Event.organizer_id == current_user.id)
        .filter(Ticket.payment_status.in_(COMMERCIAL_STATUSES))
        .scalar()
    ) or 0

    # Nombre d'events
    nb_events = (
        db.query(func.count(Event.id))
        .filter(Event.organizer_id == current_user.id)
        .scalar()
    ) or 0

    segments_stats = get_segment_stats(db, current_user.id)

    return AudienceStatsOut(
        total_contacts=total_contacts,
        total_ca_fcfa=float(total_ca),
        nb_events=nb_events,
        segments=SegmentStatsOut(**segments_stats),
    )


@router.get("/organizer/audience", response_model=AudienceListOut)
def list_audience(
    event_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    segment: Optional[str] = Query(None, pattern="^(recurrent|premium|dormant)$"),
    q: Optional[str] = Query(None, max_length=100),
    include_abandoned: bool = Query(False),
    offset: int = Query(0, ge=0, le=2000),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Liste paginée des acheteurs d'un orga.

    Filtres :
      - event_id : limiter à un event
      - status : statut précis (paye, paye_cash, reservation_cash, en_attente_om, etc.)
      - segment : recurrent|premium|dormant (filtre post-query car calculé)
      - q : recherche LIKE sur nom/téléphone/email
      - include_abandoned : inclure expire_abandonne + echec_paiement

    Cap hard : offset max 2000 (sinon export CSV recommandé).
    """
    _assert_organizer(current_user)

    base_query = _build_audience_query(
        db, current_user.id, event_id, status, include_abandoned, q
    )

    # Calcul segments AVANT filtrage segment (on a besoin de la map complète)
    segments_map = compute_segments(db, current_user.id)

    # Si filtre segment : on récupère les user_ids éligibles
    if segment:
        eligible_user_ids = {
            uid for uid, segs in segments_map.items() if segment in segs
        }
        if not eligible_user_ids:
            return AudienceListOut(
                contacts=[], total=0, offset=offset, limit=limit, has_more=False
            )
        base_query = base_query.filter(Ticket.user_id.in_(eligible_user_ids))

    # Count total AVANT pagination
    total = base_query.count()

    # Pagination + ordre (plus récents d'abord)
    tickets = (
        base_query
        .order_by(Ticket.purchased_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    contacts = [_ticket_to_contact(t, segments_map) for t in tickets]
    has_more = (offset + len(contacts)) < total

    return AudienceListOut(
        contacts=contacts,
        total=total,
        offset=offset,
        limit=limit,
        has_more=has_more,
    )


@router.get("/organizer/audience/export")
def export_audience_csv(
    request: Request,
    event_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    segment: Optional[str] = Query(None, pattern="^(recurrent|premium|dormant)$"),
    q: Optional[str] = Query(None, max_length=100),
    include_abandoned: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Export CSV streamé des acheteurs d'un orga.
    Mêmes filtres que /organizer/audience.

    Encodage UTF-8 BOM pour compatibilité Excel.
    Première ligne = commentaire mention légale.
    Log obligatoire dans audience_export_logs (preuve juridique).
    """
    _assert_organizer(current_user)

    base_query = _build_audience_query(
        db, current_user.id, event_id, status, include_abandoned, q
    )

    segments_map = compute_segments(db, current_user.id)

    if segment:
        eligible_user_ids = {
            uid for uid, segs in segments_map.items() if segment in segs
        }
        if not eligible_user_ids:
            tickets = []
        else:
            base_query = base_query.filter(Ticket.user_id.in_(eligible_user_ids))
            tickets = base_query.order_by(Ticket.purchased_at.desc()).all()
    else:
        tickets = base_query.order_by(Ticket.purchased_at.desc()).all()

    # ─── Génération CSV en mémoire ──────────────────────────────
    buffer = io.StringIO()
    # BOM UTF-8 pour Excel
    buffer.write("\ufeff")

    # Ligne 1 : mention légale
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    org_name = current_user.org_name or current_user.full_name or f"Orga #{current_user.id}"
    buffer.write(
        f"# Export GoEvent du {now_str} - "
        f"Reserve a {org_name}. "
        f"Toute revente ou transfert de cette base est interdit. "
        f"Telechargement trace.\n"
    )

    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)

    # Header
    writer.writerow([
        "nom", "telephone", "email",
        "event_titre", "event_date", "categorie",
        "prix_paye_fcfa", "statut", "methode_paiement",
        "date_achat", "segments", "ticket_id",
    ])

    # Lignes
    for t in tickets:
        contact = _ticket_to_contact(t, segments_map)
        writer.writerow([
            contact.nom,
            contact.telephone or "",
            contact.email or "",
            contact.event_titre,
            contact.event_date.strftime("%Y-%m-%d %H:%M") if contact.event_date else "",
            contact.categorie or "",
            int(contact.prix_paye_fcfa),
            contact.statut,
            contact.methode_paiement or "",
            contact.date_achat.strftime("%Y-%m-%d %H:%M") if contact.date_achat else "",
            ",".join(contact.segments),
            contact.ticket_id,
        ])

    # ─── Log de l'export (preuve juridique) ─────────────────────
    filters_str = ",".join(
        f"{k}={v}" for k, v in {
            "event_id": event_id,
            "status": status,
            "segment": segment,
            "q": q,
            "include_abandoned": include_abandoned,
        }.items() if v not in (None, False)
    )[:500]

    from core.client_ip import client_ip
    ip = client_ip(request)
    ua = (request.headers.get("user-agent") or "")[:500]

    log_entry = AudienceExportLog(
        organizer_id=current_user.id,
        event_id=event_id,
        nb_lignes=len(tickets),
        ip_address=ip,
        user_agent=ua,
        filters_applied=filters_str or None,
    )
    db.add(log_entry)
    db.commit()

    log_security_event(
        db, "audience_exported", request, current_user.id,
        f"Export {len(tickets)} contacts (filters: {filters_str or 'aucun'})",
    )

    # ─── Réponse streamée ───────────────────────────────────────
    buffer.seek(0)
    safe_org = "".join(c if c.isalnum() else "_" for c in org_name)[:40]
    date_str = datetime.utcnow().strftime("%Y%m%d_%H%M")
    filename = f"audience_{safe_org}_{date_str}.csv"

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/organizer/audience/segments", response_model=SegmentStatsOut)
def get_segments_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stats des 3 segments pour cet orga (alimente les cards cliquables)."""
    _assert_organizer(current_user)
    return SegmentStatsOut(**get_segment_stats(db, current_user.id))
