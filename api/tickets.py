"""
api/tickets.py
Routeur billets : billet gratuit, scan & validation d'entrée, listes fan.
Paiement en ligne uniquement (Orange Money via api/payments.py) — le flux
cash payant (réservation / encaissement / annulation) est supprimé (oct. 2026).
"""
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi import BackgroundTasks
from core.email import envoyer_billet_email
from fastapi import Request
from core.limiter import PUBLIC_IP_CEILING, limiter, qr_hash_key, user_key

from utils.serializers import utc_iso
from core.orders import MAX_FREE_PER_ACCOUNT, assert_account_limit, assert_can_buy, assert_capacity, assert_not_started
from utils.locks import lock_event_for_booking
from utils.promo import lock_promo_for_use, compute_discount
from core import invitations
from core.database import get_db
from core.security import get_current_user, log_security_event
from models.event import Event
from models.ticket import Ticket
from models.user import User
from models.promo import PromoUsage
from schemas.ticket import ScanTicket, CashReservation, HolderIn
from utils.slugs import event_id_to_slug

router = APIRouter()


# ── LISTES FAN ──────────────────────────────────────────────────
@router.get("/tickets/my")
def my_tickets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tickets = (
        db.query(Ticket)
        .filter(Ticket.user_id == current_user.id)
        .order_by(Ticket.purchased_at.desc(), Ticket.id)
        .all()
    )
    # Commande de N billets (lot 5.5) : rang « 2 sur 4 » au sein du même payment_ref
    orders = {}
    for t in sorted(tickets, key=lambda x: x.id):
        orders.setdefault(t.payment_ref or "t%d" % t.id, []).append(t.id)
    return [{
        "ticket_id": t.id,
        "qr_hash": t.qr_hash,
        "payment_status": t.payment_status,
        "payment_method": t.payment_method,
        "event_id": t.event_id,
        "event_title": t.event.title if t.event else "",
        "event_date": utc_iso(t.event.event_date) if t.event else None,
        "event_location": t.event.location if t.event else "",
        "price": t.cash_amount if t.cash_amount else (t.event.price if t.event else 0),
        "is_used": t.is_used,
        "used_at": t.used_at,
        "purchased_at": t.purchased_at,
        "failure_reason": t.failure_reason,
        "order_ref": t.payment_ref,
        "order_size": len(orders[t.payment_ref or "t%d" % t.id]),
        "order_index": orders[t.payment_ref or "t%d" % t.id].index(t.id) + 1,
        "tier_name": t.tier_name,
        "holder_label": t.holder_label,
        "shared_at": t.shared_at,
    } for t in tickets]


# ── ENVOI DES BILLETS D'UNE COMMANDE (lot 5.5) ──────────────────
def _my_ticket(db: Session, ticket_id: int, user: User, lock: bool = False) -> Ticket:
    q = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.user_id == user.id)
    if lock:
        q = q.with_for_update()
    t = q.first()
    if not t:
        raise HTTPException(404, "Billet introuvable")
    return t


@router.patch("/tickets/{ticket_id}/holder")
@limiter.limit("60/minute", key_func=user_key)
def set_ticket_holder(request: Request, ticket_id: int, data: HolderIn, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """Prénom facultatif « Pour : … » (affiché sur le pass, jamais contrôlé à l'entrée)."""
    t = _my_ticket(db, ticket_id, current_user, lock=True)
    label = " ".join("".join(ch for ch in (data.holder_label or "") if ord(ch) >= 32).split())[:40]
    t.holder_label = label or None
    db.commit()
    return {"ticket_id": t.id, "holder_label": t.holder_label}


@router.post("/tickets/{ticket_id}/shared")
@limiter.limit("60/minute", key_func=user_key)
def mark_ticket_shared(request: Request, ticket_id: int, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Marque le billet « envoyé » (bouton WhatsApp de « Mes billets »)."""
    t = _my_ticket(db, ticket_id, current_user, lock=True)
    if t.payment_status not in ("paye", "paye_cash"):
        raise HTTPException(409, "Billet non validé : rien à envoyer.")
    t.shared_at = datetime.utcnow()
    db.commit()
    return {"ticket_id": t.id, "shared_at": t.shared_at}


@router.post("/tickets/{ticket_id}/reissue")
@limiter.limit("10/minute", key_func=user_key)
def reissue_ticket(request: Request, ticket_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """« Renvoyer un nouveau lien » : nouveau QR, l'ancien lien ne permet plus d'entrer.
    Seulement si le billet est payé, pas encore scanné et l'événement pas terminé."""
    t = _my_ticket(db, ticket_id, current_user, lock=True)
    if t.payment_status not in ("paye", "paye_cash"):
        raise HTTPException(409, "Billet non validé.")
    if t.is_used:
        raise HTTPException(409, "Billet déjà scanné à l'entrée : impossible de le remplacer.")
    if t.event and t.event.event_date and t.event.event_date < datetime.utcnow() - timedelta(hours=6):
        raise HTTPException(409, "Événement terminé.")
    old = t.qr_hash
    t.qr_hash = str(uuid.uuid4())
    t.shared_at = None
    db.commit()
    log_security_event(db, "ticket_reissued", request, current_user.id,
                       "billet #%d · ancien QR %s… remplacé" % (t.id, (old or "")[:8]))
    return {"ticket_id": t.id, "qr_hash": t.qr_hash}


@router.get("/tickets/paid")
def my_paid_tickets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tickets = (
        db.query(Ticket)
        .filter(Ticket.user_id == current_user.id, Ticket.payment_status == "paye")
        .order_by(Ticket.purchased_at.desc())
        .all()
    )
    return [{
        "ticket_id": t.id, "qr_hash": t.qr_hash,
        "event_title": t.event.title if t.event else "",
        "event_date": utc_iso(t.event.event_date) if t.event else None,
        "event_location": t.event.location if t.event else "",
        "price": t.event.price if t.event else 0,
        "is_used": t.is_used,
        "purchased_at": t.purchased_at,
    } for t in tickets]


# ── BILLET GRATUIT (event gratuit ou code promo -100 %) ─────────
# Oct. 2026 : la RÉSERVATION CASH PAYANTE EST SUPPRIMÉE (paiement en ligne
# uniquement). Cette route ne délivre plus que des billets à 0 FCFA.
# /tickets/reserve-cash conservée comme alias : PWA en cache côté fans.
@router.post("/tickets/reserve-free")
@router.post("/tickets/reserve-cash")
@limiter.limit("20/minute")
def reserve_free_ticket(request: Request,
                        data: CashReservation,
                        background_tasks: BackgroundTasks,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    assert_can_buy(current_user)          # comptes fan (et admin) uniquement
    # Verrou pessimiste + check quota atomique (anti-survente)
    event = lock_event_for_booking(db, data.event_id)
    assert_not_started(event)

    # Anti-accaparement des places gratuites : 3 billets max par compte et par event
    # (lot 5.5 : jusqu'à 3 en une seule demande).
    qty = data.quantity
    assert_capacity(event, qty)
    assert_account_limit(db, current_user.id, event.id, qty, MAX_FREE_PER_ACCOUNT, ("paye", "paye_cash"))

    prix_base = float(event.price)
    if data.tier_name and event.ticket_tiers:
        tier = next((t for t in event.ticket_tiers if t.get("name") == data.tier_name), None)
        if not tier:
            raise HTTPException(400, "Catégorie de billet invalide")
        try:
            prix_base = float(tier.get("price", event.price))
        except (ValueError, TypeError):
            raise HTTPException(400, "Catégorie de billet invalide")

    # Code promo : seul moyen de rendre gratuit un billet payant.
    promo_obj = None
    discount_fcfa = 0
    if data.promo_code:
        promo_obj = lock_promo_for_use(
            db,
            user_id=current_user.id,
            event_id=event.id,
            tier_name=data.tier_name,
            code=data.promo_code,
            organizer_id=event.organizer_id,
            quantity=qty,
        )
        discount_fcfa = compute_discount(promo_obj, prix_base)
        prix_base = max(0.0, prix_base - discount_fcfa)

    if prix_base > 0:
        # Plus de cash : tout billet payant passe par Orange Money.
        # (Le rollback de la session libère les verrous event/promo.)
        raise HTTPException(400, "Ce billet est payant : réglez-le par Orange Money.")

    if data.email and not current_user.email:
        current_user.email = data.email

    order_ref = f"FREE-{uuid.uuid4().hex[:8].upper()}"
    tickets = [Ticket(
        event_id=event.id,
        user_id=current_user.id,
        payment_status="paye",
        payment_method="gratuit",
        payment_ref=order_ref,
        cash_amount=0,
        tier_name=data.tier_name if data.tier_name and event.ticket_tiers else None,
    ) for _ in range(qty)]
    db.add_all(tickets)
    event.seats_sold += qty  # sous verrou lock_event_for_booking
    db.flush()  # ids avant les PromoUsage

    if promo_obj:
        for t in tickets:
            db.add(PromoUsage(
                promo_id=promo_obj.id,
                user_id=current_user.id,
                ticket_id=t.id,
                discount_fcfa=discount_fcfa,
                status="confirmed",
            ))
        promo_obj.used_count += qty

    db.commit()
    for t in tickets:
        db.refresh(t)
    new_ticket = tickets[0]

    log_security_event(db, "free_ticket", None, current_user.id,
                       f"{qty} billet(s) gratuit(s) événement {event.id}" + (f" (promo {promo_obj.id})" if promo_obj else ""))
    if current_user.email:
        background_tasks.add_task(
            envoyer_billet_email,
            email_client=current_user.email,
            nom_client=current_user.full_name or "Client",
            ticket=new_ticket,
            event=event,
            amount=0,
            tickets=tickets,
        )

    return {
        "success": True,
        "message": "Billet gratuit confirmé.",
        "ticket_id": new_ticket.id,
        "ticket_ids": [t.id for t in tickets],
        "quantity": qty,
        "qr_hash": new_ticket.qr_hash,
        "amount_to_pay": 0,
        "event_title": event.title,
        "event_date": utc_iso(event.event_date),
        "instructions": "Présentez ce QR code à l'entrée de l'événement.",
    }


def _can_operate_event(user: User, event: Event) -> bool:
    """
    Contrôle d'accès opérationnel jour J (scan, lecture billet).
    organizer : propriétaire de l'event. agent : assigné à CET event
    (agent_event_id n'est posé que par l'orga propriétaire, cf create_agent).
    org_name n'est PAS un critère : champ éditable via PUT /auth/me.
    """
    if event is None:
        return False
    if user.role in ("organizer", "organisation"):
        return event.organizer_id == user.id
    if user.role == "agent":
        return user.agent_event_id is not None and user.agent_event_id == event.id
    return False


# ── SCAN & VALIDATION D'ENTRÉE ──────────────────────────────────
@router.post("/scan")
@limiter.limit("120/minute", key_func=user_key)   # par agent : toute l'équipe partage le Wi-Fi de la salle
def valider_billet(request: Request, data: ScanTicket, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    if current_user.role not in ("agent", "organizer", "organisation"):
        raise HTTPException(status_code=403, detail="Accès refusé. Réservé aux contrôleurs.")

    # Pass d'invitation (QR = URL goevent.africa/i/<jeton>) : branche dédiée,
    # mêmes contrôles d'accès, places multiples (cf. core/invitations.scan)
    invitation_token = invitations.parse_pass_qr(data.qr_data)
    if invitation_token:
        return invitations.scan(db, current_user, invitation_token, data.count, request, data.client_uuid)

    # Verrou ligne : 2 agents qui scannent le même QR simultanément →
    # un seul passe, l'autre voit "DÉJÀ UTILISÉ".
    ticket = (
        db.query(Ticket)
        .filter(Ticket.qr_hash == data.qr_data)
        .with_for_update()
        .first()
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Billet introuvable (Faux billet ou QR mal lu).")

    # Fix NameError `event` (agents → 500 systématique depuis juin) +
    # scoping orga : avant, tout orga pouvait "brûler" les billets de n'importe quel event.
    if not _can_operate_event(current_user, ticket.event):
        log_security_event(db, "scan_forbidden", request, current_user.id,
                           f"Scan refusé ticket {ticket.id} (event {ticket.event_id})")
        raise HTTPException(403, "Ce billet appartient à un autre événement.")

    if ticket.is_used:
        date_used = ticket.used_at.strftime("%H:%M:%S") if ticket.used_at else "?"
        raise HTTPException(status_code=400, detail=f"Alerte : Billet DÉJÀ UTILISÉ à {date_used} !")

    if ticket.payment_status not in ("paye", "paye_cash"):
        raise HTTPException(status_code=400, detail=f"Billet non valide (statut: {ticket.payment_status}).")

    try:
        ticket.is_used = True
        ticket.used_at = datetime.utcnow()
        db.commit()
        return {
            "valid": True,
            "requires_cash_payment": False,
            "message": f"Validé ! {ticket.owner.full_name} peut entrer.",
            "event": ticket.event.title,
            "payment_method": ticket.payment_method or "online",
        }
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur interne lors de la sauvegarde.")


@router.get("/tickets/scan/stats/{event_id}")
def scan_stats(event_id: int, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    # P0 — Restriction : seul agent assigné / organizer de l'event / admin
    if current_user.role == "admin":
        pass
    elif current_user.role in ("organizer", "organisation"):
        if event.organizer_id != current_user.id:
            raise HTTPException(403, "Cet événement n'est pas le vôtre")
    elif current_user.role == "agent":
        # agent_event_id n'est posé que par l'orga propriétaire (create_agent) : seul critère.
        # (org_name retiré : éditable via PUT /auth/me → 403 dès que l'orga se renommait)
        if current_user.agent_event_id != event.id:
            raise HTTPException(403, "Vous n'êtes pas assigné à cet événement")
    else:
        raise HTTPException(403, "Accès refusé")

    if event.visibility == "private":   # invitations : personnes (places), pas billets
        s = invitations.stats(db, event_id)
        return {"event_title": event.title, "total_confirmed": s["expected"], "total_entered": s["entered"],
                "total_remaining": s["remaining"], "entry_rate_pct": s["rate"],
                "billets_vendus": s["expected"], "personnes_entrees": s["entered"],
                "personnes_restantes": s["remaining"], "taux_entree": f"{s['rate']:.1f}%"}

    # Billets validés = paye (NGSER + gratuits ont ce statut) + paye_cash (legacy)
    total_confirmed = db.query(Ticket).filter(
        Ticket.event_id == event_id,
        Ticket.payment_status.in_(["paye", "paye_cash"]),
    ).count()

    # Personnes déjà scannées et entrées
    total_entered = db.query(Ticket).filter(
        Ticket.event_id == event_id,
        Ticket.is_used == True,
    ).count()

    total_remaining = max(0, total_confirmed - total_entered)
    taux = round((total_entered / total_confirmed * 100), 1) if total_confirmed else 0.0

    return {
        "event_title": event.title,
        "total_confirmed": total_confirmed,
        "total_entered": total_entered,
        "total_remaining": total_remaining,
        "entry_rate_pct": taux,
        # Compatibilité ancien contrat
        "billets_vendus": total_confirmed,
        "personnes_entrees": total_entered,
        "personnes_restantes": total_remaining,
        "taux_entree": f"{taux:.1f}%",
    }

# ── ROUTE PUBLIQUE : consultation billet sans authentification ──
# Lien de secours pour fan qui a perdu accès à son compte (perte téléphone,
# oubli PIN, achat pour un tiers). qr_hash est un UUID v4 128 bits, non-devinable
# en pratique — rate limit 30/min pour éviter énumération brute-force résiduelle.
@router.get("/ticket/view")
@limiter.limit("30/minute", key_func=qr_hash_key)
@limiter.shared_limit(PUBLIC_IP_CEILING, scope="public_token_pages")
def view_ticket_public(request: Request, qr_hash: str, db: Session = Depends(get_db)):
    """
    Retourne les infos d'un billet à partir de son qr_hash, sans auth.
    Utilisée par ticket_view.html (lien magique dans l'email confirmation).
    """
    ticket = db.query(Ticket).filter(Ticket.qr_hash == qr_hash).first()
    if not ticket:
        raise HTTPException(404, "Billet introuvable")

    if ticket.payment_status not in ("paye", "paye_cash"):
        raise HTTPException(409, "Ce billet n'est pas encore validé")

    event = db.query(Event).filter(Event.id == ticket.event_id).first()

    same = [x[0] for x in db.query(Ticket.id).filter(Ticket.payment_ref == ticket.payment_ref).order_by(Ticket.id).all()] \
        if ticket.payment_ref else [ticket.id]
    return {
        "qr_hash": ticket.qr_hash,
        "event_id": event.id if event else None,
        "event_slug": event_id_to_slug(event.id) if event else None,
        "tier_name": ticket.tier_name,
        "holder_label": ticket.holder_label,
        "order_index": same.index(ticket.id) + 1 if ticket.id in same else 1,
        "order_size": len(same),
        "event_title": event.title if event else None,
        "event_date": utc_iso(event.event_date) if event else None,
        "event_location": event.location if event else None,
        "is_used": ticket.is_used,
        "used_at": ticket.used_at.isoformat() if ticket.used_at else None,
    }
