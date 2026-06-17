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

from core.database import get_db
from core.security import get_current_user, log_security_event
from core.ngser import create_withdrawal_session
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
def admin_update_user(user_id: int, data: dict, db: Session = Depends(get_db),
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
def admin_update_event(event_id: int, data: dict, db: Session = Depends(get_db),
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
def admin_update_partner(partner_id: int, data: dict, db: Session = Depends(get_db),
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
async def validate_payout(payout_id: int, db: Session = Depends(get_db),
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

    payout.status = "en_cours"
    db.commit()

    orga = db.query(User).filter(User.id == payout.organizer_id).first()
    phone_number = orga.orange_money if (orga and orga.orange_money) else (orga.phone_number if orga else None)
    if not phone_number:
        payout.status = "en_attente"
        db.commit()
        raise HTTPException(status_code=400, detail="Numéro de réception introuvable pour cet organisateur.")

    withdrawal_id = f"WD{payout.id}-{uuid.uuid4().hex[:10].upper()}"

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
def approve_cancellation(request_id: int, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_admin)):
    cancel_req = db.query(CancellationRequest).filter(CancellationRequest.id == request_id).first()
    if not cancel_req:
        raise HTTPException(404, "Demande introuvable")

    if cancel_req.status == "traite":
        raise HTTPException(400, "Cette demande a déjà été traitée")

    event = db.query(Event).filter(Event.id == cancel_req.event_id).first()
    event_id_for_log = cancel_req.event_id

    if event:
        cancel_req.event_id = None
        db.query(Ticket).filter(Ticket.event_id == event.id).delete()
        db.query(User).filter(User.agent_event_id == event.id).delete()
        db.delete(event)

    cancel_req.status = "traite"
    db.commit()

    log_security_event(db, "event_cancelled", request, current_user.id, f"Event #{event_id_for_log}")
    return {"message": "Événement définitivement supprimé."}

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
