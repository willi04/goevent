"""
api/admin.py
Routeur administration transverse : stats globales, gestion users/events/partners,
validation des retraits (appel NGSER B2C), demandes d'annulation, logs de sécurité.
Toutes les routes exigent role == "admin".
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from datetime import datetime
from core.database import get_db
from core.security import get_current_user, log_security_event
from core.ngser import create_withdrawal_session
from core.limiter import limiter
from models.event import Event
from models.ticket import Ticket
from models.user import User, SecurityLog
from models.payment import Payment, PayoutRequest
from models.partner import PartnerRequest, CancellationRequest
from utils.serializers import _event_dict

from core.permissions import (
    get_current_admin,
    get_current_admin_or_staff,
    require_staff_function,
)

router = APIRouter()


# ── STATS GLOBALES (filtrées selon le profil) ───────────────────
@router.get("/admin/global-stats")
def admin_global_stats(db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """
    Stats dashboard. Filtre selon profil (admin / commercial / cm / designer).
    Refuse fan/organizer/agent.
    Renvoie uniquement les champs autorisés pour le profil.
    Le frontend affiche dynamiquement les cartes correspondantes.
    """
    role = current_user.role
    fn = current_user.staff_function

    if role not in ("admin", "staff"):
        raise HTTPException(403, "Accès refusé")
    if role == "staff" and fn not in ("commercial", "cm", "designer"):
        raise HTTPException(403, "Profil staff non reconnu")

    # Imports locaux pour éviter les cycles
    from models.cms import HomepageContent
    from models.social import Feedback

    stats = {}

    # Champ commun à tous : events actifs
    stats["total_events"] = db.query(Event).filter(Event.is_active == True).count()

    # Champs admin & commercial (vision business globale)
    if role == "admin" or fn == "commercial":
        stats["total_users"] = db.query(User).filter(User.role.in_(["fan", "organizer"])).count()
        stats["total_orgas"] = db.query(User).filter(User.role == "organizer").count()
        stats["total_tickets"] = db.query(Ticket).filter(Ticket.payment_status == "paye").count()

    # Champs admin uniquement (financier sensible)
    if role == "admin":
        stats["revenue_net"] = round(
            db.query(func.sum(Payment.platform_fee))
              .filter(Payment.status == "completed").scalar() or 0, 2
        )
        stats["ca_total"] = round(
            db.query(func.sum(Payment.amount))
              .filter(Payment.status == "completed").scalar() or 0, 2
        )
        stats["pending_payouts"] = db.query(PayoutRequest).filter(
            PayoutRequest.status == "en_attente"
        ).count()

    # Champs CM & designer (visuels)
    if role == "admin" or fn in ("cm", "designer"):
        stats["slides_active"] = db.query(HomepageContent).filter(
            HomepageContent.is_active == True
        ).count()

    # Champ CM (modération communautaire)
    if role == "admin" or fn == "cm":
        stats["feedbacks_new"] = db.query(Feedback).filter(
            Feedback.status == "nouveau"
        ).count()

    return stats


# ── USERS ───────────────────────────────────────────────────────
@router.get("/admin/users")
def admin_list_users(db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_admin_or_staff)):
    # Admin voit tout. Commercial uniquement (besoin métier : démarche orgas).
    if current_user.role == "staff" and current_user.staff_function != "commercial":
        raise HTTPException(403, "Réservé aux commerciaux")
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [{
        "id": u.id, "name": u.full_name, "phone": u.phone_number,
        "email": u.email, "role": u.role, "is_active": u.is_active,
        "org_name": u.org_name, "org_type": u.org_type,
        "created_at": u.created_at,
    } for u in users]


@router.put("/admin/users/{user_id}")
@limiter.limit("30/minute")
def admin_update_user(request: Request, user_id: int, data: dict, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")

    if "is_active" in data:
        nouveau_statut = data["is_active"]
        user.is_active = nouveau_statut
        if user.role in ["organizer", "organisation"]:
            db.query(Event).filter(Event.organizer_id == user.id).update(
                {"is_active": nouveau_statut}
            )

    db.commit()
    return {"message": "Utilisateur et ses événements mis à jour"}


# ── EVENTS ──────────────────────────────────────────────────────
@router.get("/admin/events")
def admin_list_events(db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_admin_or_staff)):
    events = db.query(Event).order_by(Event.created_at.desc()).all()
    return [_event_dict(e) for e in events]


@router.put("/admin/events/{event_id}")
@limiter.limit("30/minute")
def admin_update_event(request: Request, event_id: int, data: dict, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_admin)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    if "is_active" in data:
        event.is_active = data["is_active"]
    db.commit()
    return {"message": "Événement mis à jour"}

# ── PARTENAIRES ─────────────────────────────────────────────────
@router.get("/admin/partners")
def admin_list_partners(db: Session = Depends(get_db),
                        current_user: User = Depends(require_staff_function("commercial"))):
    partners = db.query(PartnerRequest).order_by(PartnerRequest.created_at.desc()).all()
    return [{
        "id": p.id, "company_name": p.company_name, "email": p.email,
        "phone": p.phone, "partnership_type": p.partnership_type,
        "message": p.message, "status": p.status, "created_at": p.created_at,
    } for p in partners]

@router.put("/admin/partners/{partner_id}")
@limiter.limit("30/minute")
def admin_update_partner(request: Request, partner_id: int, data: dict, db: Session = Depends(get_db),
                         current_user: User = Depends(require_staff_function("commercial"))):
    partner = db.query(PartnerRequest).filter(PartnerRequest.id == partner_id).first()
    if not partner:
        raise HTTPException(404, "Demande introuvable")
    if "status" in data:
        partner.status = data["status"]
    db.commit()
    return {"message": "Demande mise à jour"}

# ── RETRAITS (PAYOUTS) ──────────────────────────────────────────
@router.get("/admin/payouts")
async def get_all_payouts(db: Session = Depends(get_db),
                          current_user: User = Depends(require_staff_function("commercial"))):
    payouts = db.query(PayoutRequest).order_by(PayoutRequest.created_at.desc()).all()

    # Résolution des organisateurs en UNE requête (anti N+1)
    org_ids = {p.organizer_id for p in payouts}
    orgs_map = {}
    if org_ids:
        orgs = db.query(User).filter(User.id.in_(org_ids)).all()
        orgs_map = {u.id: u for u in orgs}

    result = []
    for p in payouts:
        orga = orgs_map.get(p.organizer_id)
        result.append({
            "id": p.id,
            "event_id": p.event_id,
            "amount": p.amount,
            "status": p.status,
            "org_name": (orga.org_name or orga.full_name) if orga else "Inconnu",
            "phone_number": (orga.orange_money or orga.phone_number) if orga else "Non renseigné",
            "created_at": p.created_at,
        })
    return result

@router.post("/admin/payouts/{payout_id}/validate")
@limiter.limit("10/minute")
async def validate_payout(request: Request, payout_id: int, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_admin)):
    # 🔒 SORTIE D'ARGENT : RÉSERVÉ ADMIN STRICT.
    # Même un commercial ne valide PAS les payouts. Politique : un seul humain
    # peut déclencher un virement, et il a un compte admin dédié.

    payout = (
        db.query(PayoutRequest)
        .filter(PayoutRequest.id == payout_id)
        .with_for_update()
        .first()
    )
    if not payout:
        raise HTTPException(status_code=404, detail="Demande de retrait introuvable")
    if payout.status == "paye":
        return {"message": "Ce versement a déjà été effectué"}
    if payout.status == "en_cours":
        raise HTTPException(status_code=409, detail="Un virement est déjà en cours pour cette demande.")
    if payout.status != "en_attente":
        raise HTTPException(status_code=400, detail=f"Statut non traitable : {payout.status}")

    # Génération de l'order_id AVANT la transition de statut pour persister les
    # deux atomiquement (invariant : status=en_cours ⇒ payment_ref non-null,
    # indispensable au filet cron reconcile).
    withdrawal_id = f"WD{payout.id}-{uuid.uuid4().hex[:10].upper()}"
    payout.status = "en_cours"
    payout.payment_ref = withdrawal_id
    payout.processed_at = datetime.utcnow()
    db.commit()

    orga = db.query(User).filter(User.id == payout.organizer_id).first()
    phone_number = orga.orange_money if (orga and orga.orange_money) else (orga.phone_number if orga else None)
    if not phone_number:
        payout.status = "en_attente"
        db.commit()
        raise HTTPException(status_code=400, detail="Numéro de réception introuvable pour cet organisateur.")

    try:
        status_code, response_data, raw_text = await create_withdrawal_session(
            order_id=withdrawal_id,
            amount=int(payout.amount),
            customer_phone=phone_number,
        )

        print(f"📡 [NGSER RETRAIT RÉPONSE BRUTE] Code HTTP: {status_code} | Contenu: {raw_text}")

        if status_code == 200 and "payment_url" in response_data:
            print(f"🔗 Session de retrait créée : {response_data['payment_url']}")
            return {
                "status": "success",
                "message": "Session de retrait générée.",
                "payment_url": response_data["payment_url"],
            }
        elif status_code == 200 and response_data.get("status_id") == 1:
            payout.status = "paye"
            db.commit()
            return {"status": "success", "message": f"Retrait de {payout.amount} FCFA effectué"}
        else:
            payout.status = "en_attente"
            db.commit()
            error_msg = response_data.get("description", f"Erreur NGSER (Code HTTP {status_code})")
            raise HTTPException(status_code=400, detail=f"Échec : {error_msg}")

    except HTTPException:
        raise
    except Exception as e:
        payout.status = "en_attente"
        db.commit()
        print(f"❌ Erreur Interne Retrait : {str(e)}")
        raise HTTPException(status_code=503, detail="Le service de transfert est temporairement indisponible.")


# ── ANNULATIONS ─────────────────────────────────────────────────
@router.get("/admin/cancellations")
def get_admin_cancellations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(403, "Accès refusé")

    requests = db.query(CancellationRequest).order_by(CancellationRequest.created_at.desc()).all()
    return [{
        "id": r.id,
        "event_title": r.event.title if r.event else "Événement supprimé",
        "organizer_name": r.organizer.org_name or r.organizer.full_name,
        "reason": r.reason,
        "status": r.status,
        "created_at": r.created_at,
    } for r in requests]


@router.post("/admin/cancellations/{request_id}/approve")
@limiter.limit("20/minute")
def approve_cancellation(request_id: int, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_admin)):
    """
    Annulation propre avec création des demandes de remboursement.
    - Refuse si un payout 'paye' existe (l'orga a déjà touché → cas litigieux à traiter manuellement)
    - Crée 1 RefundRequest par ticket payé en ligne (paye + paye_cash)
    - Email "événement annulé" pour tous les acheteurs (payés + réservations cash)
    - Ne supprime RIEN : event passe is_active=False, status sémantique 'annule'
    """
    from models.refund import RefundRequest
    from core.email import envoyer_mail_annulation
    
    cancel_req = db.query(CancellationRequest).filter(CancellationRequest.id == request_id).first()
    if not cancel_req:
        raise HTTPException(404, "Demande introuvable")
    if cancel_req.status == "traite":
        raise HTTPException(400, "Cette demande a déjà été traitée")

    event = db.query(Event).filter(Event.id == cancel_req.event_id).with_for_update().first()
    if not event:
        raise HTTPException(404, "Événement introuvable")

    # 🔒 GARDE : si un payout a été versé, on bloque
    payout_paye = db.query(PayoutRequest).filter(
        PayoutRequest.event_id == event.id,
        PayoutRequest.status == "paye",
    ).first()
    if payout_paye:
        raise HTTPException(
            409,
            f"Impossible d'annuler : un retrait de {int(payout_paye.amount)} FCFA "
            f"a déjà été versé à l'organisateur. Récupérez d'abord les fonds, "
            f"puis traitez manuellement."
        )

    # 🔒 GARDE : annuler les payouts en_attente sur cet event
    db.query(PayoutRequest).filter(
        PayoutRequest.event_id == event.id,
        PayoutRequest.status == "en_attente",
    ).update({"status": "annule"})

    # Récupération des tickets concernés (payés ou réservés cash)
    tickets_paye_online = db.query(Ticket).filter(
        Ticket.event_id == event.id,
        Ticket.payment_status == "paye",
    ).all()
    
    tickets_paye_cash = db.query(Ticket).filter(
        Ticket.event_id == event.id,
        Ticket.payment_status == "paye_cash",
    ).all()
    
    tickets_reservation_cash = db.query(Ticket).filter(
        Ticket.event_id == event.id,
        Ticket.payment_status == "reservation_cash",
    ).all()

    # Résolution des users en une requête (anti N+1)
    user_ids = {t.user_id for t in tickets_paye_online + tickets_paye_cash + tickets_reservation_cash}
    users_map = {}
    if user_ids:
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        users_map = {u.id: u for u in users}

    refunds_created = 0
    emails_queued = []

    # 1. Remboursements pour les paiements Orange Money en ligne
    #    On rembourse uniquement le prix de base (pas les 4% frais GoEvent)
    for ticket in tickets_paye_online:
        user = users_map.get(ticket.user_id)
        if not user:
            continue
        
        payment = db.query(Payment).filter(Payment.ticket_id == ticket.id).first()
        prix_base = payment.base_price if payment else (ticket.cash_amount or event.price)
        
        refund = RefundRequest(
            ticket_id=ticket.id,
            user_id=user.id,
            event_id=event.id,
            event_title=event.title,
            amount=prix_base,
            phone_number=user.orange_money or user.phone_number,
            status="a_rembourser",
        )
        db.add(refund)
        ticket.payment_status = "annule_rembourse_en_attente"
        if payment:
            payment.status = "refund_pending"
        refunds_created += 1
        emails_queued.append((user.email, user.full_name, event.title, prix_base, True))

    # 2. Remboursements pour les paiements cash (encaissés par l'agent)
    for ticket in tickets_paye_cash:
        user = users_map.get(ticket.user_id)
        if not user:
            continue
        prix_cash = ticket.cash_amount or event.price
        
        refund = RefundRequest(
            ticket_id=ticket.id,
            user_id=user.id,
            event_id=event.id,
            event_title=event.title,
            amount=prix_cash,
            phone_number=user.orange_money or user.phone_number,
            status="a_rembourser",
            admin_note="Payé cash à l'agent — remboursement à effectuer par l'organisateur ou GoEvent",
        )
        db.add(refund)
        ticket.payment_status = "annule_rembourse_en_attente"
        refunds_created += 1
        emails_queued.append((user.email, user.full_name, event.title, prix_cash, True))

    # 3. Réservations cash : juste libérer + email, pas de remboursement
    for ticket in tickets_reservation_cash:
        user = users_map.get(ticket.user_id)
        ticket.payment_status = "annule"
        if user:
            emails_queued.append((user.email, user.full_name, event.title, 0, False))

    # 4. Désactivation de l'event (PAS de suppression)
    event.is_active = False
    cancel_req.status = "traite"
    db.commit()

    # 5. Envoi des emails (best-effort, ne bloque pas la transaction)
    for email, name, title, amount, is_refund in emails_queued:
        if email:
            try:
                envoyer_mail_annulation(email, name, title, amount, is_refund)
            except Exception as e:
                print(f"⚠️ Email annulation échoué pour {email}: {e}")

    log_security_event(db, "event_cancelled_with_refunds", request, current_user.id,
                       f"Event #{event.id} annulé, {refunds_created} remboursements créés")

    return {
        "message": "Annulation traitée.",
        "refunds_created": refunds_created,
        "emails_sent": len(emails_queued),
    }
                             
# ── LOGS DE SÉCURITÉ ────────────────────────────────────────────
@router.get("/admin/security-logs")
def get_security_logs(
    limit: int = 100,
    event_type: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    query = db.query(SecurityLog).order_by(SecurityLog.created_at.desc())
    if event_type:
        query = query.filter(SecurityLog.event_type == event_type)
    logs = query.limit(min(limit, 500)).all()

    return [{
        "id": l.id,
        "event_type": l.event_type,
        "user_id": l.user_id,
        "ip_address": l.ip_address,
        "user_agent": l.user_agent[:100] if l.user_agent else None,
        "details": l.details,
        "created_at": l.created_at,
    } for l in logs]


# ── REMBOURSEMENTS ──────────────────────────────────────────────
@router.get("/admin/refunds")
def list_refunds(
    status: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Liste des remboursements à effectuer / effectués. Admin strict (sortie d'argent)."""
    from models.refund import RefundRequest
    
    q = db.query(RefundRequest).order_by(RefundRequest.created_at.desc())
    if status:
        q = q.filter(RefundRequest.status == status)
    refunds = q.all()
    
    # Anti N+1 sur les users
    user_ids = {r.user_id for r in refunds}
    users_map = {}
    if user_ids:
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        users_map = {u.id: u for u in users}
    
    stats = {
        "total": db.query(RefundRequest).count(),
        "a_rembourser": db.query(RefundRequest).filter(RefundRequest.status == "a_rembourser").count(),
        "paye": db.query(RefundRequest).filter(RefundRequest.status == "paye").count(),
    }
    montant_a_payer = db.query(func.sum(RefundRequest.amount)).filter(
        RefundRequest.status == "a_rembourser"
    ).scalar() or 0
    stats["amount_pending"] = float(montant_a_payer)
    
    items = []
    for r in refunds:
        u = users_map.get(r.user_id)
        items.append({
            "id": r.id,
            "ticket_id": r.ticket_id,
            "user_name": u.full_name if u else "Utilisateur supprimé",
            "user_email": u.email if u else None,
            "event_id": r.event_id,
            "event_title": r.event_title,
            "amount": r.amount,
            "phone_number": r.phone_number,
            "status": r.status,
            "payment_proof": r.payment_proof,
            "admin_note": r.admin_note,
            "created_at": r.created_at.isoformat(),
            "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        })
    
    return {"items": items, "stats": stats}


@router.post("/admin/refunds/{refund_id}/mark-paid")
@limiter.limit("20/minute")
def mark_refund_paid(
    request: Request,
    refund_id: int,
    data: dict,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Marque un remboursement comme effectué manuellement. Admin strict."""
    from models.refund import RefundRequest
    
    payment_proof = (data.get("payment_proof") or "").strip()
    if not payment_proof or len(payment_proof) < 4:
        raise HTTPException(400, "Le numéro de transaction Orange Money est requis (min 4 caractères)")
    
    refund = db.query(RefundRequest).filter(RefundRequest.id == refund_id).with_for_update().first()
    if not refund:
        raise HTTPException(404, "Remboursement introuvable")
    if refund.status == "paye":
        raise HTTPException(400, "Ce remboursement a déjà été marqué payé")
    if refund.status == "annule":
        raise HTTPException(400, "Ce remboursement a été annulé")
    
    refund.status = "paye"
    refund.payment_proof = payment_proof
    refund.admin_note = (data.get("admin_note") or "").strip() or refund.admin_note
    refund.paid_at = datetime.utcnow()
    refund.paid_by = current_user.id
    
    # Marquer le ticket comme définitivement remboursé
    ticket = db.query(Ticket).filter(Ticket.id == refund.ticket_id).first()
    if ticket:
        ticket.payment_status = "annule_rembourse"
    
    payment = db.query(Payment).filter(Payment.ticket_id == refund.ticket_id).first()
    if payment:
        payment.status = "refunded"
    
    db.commit()
    
    log_security_event(db, "refund_paid", request, current_user.id,
                       f"Refund #{refund.id} payé ({refund.amount} FCFA, ref={payment_proof})")
    
    return {"status": "success", "message": "Remboursement marqué comme effectué"}
