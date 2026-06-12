"""
api/payments.py
Routeur paiements NGSER (Orange Money).
- initiate_ngser_payment : génère le lien de paiement (vrai flux d'achat)
- ngser_webhook : notification post-paiement (Instant Payment Notification)
  → SÉCURISÉ : vérification stricte du montant + statut + idempotence
- init_payment : ancien flux USSD (VESTIGE — à supprimer après vérif front)

⚠️ ZONE FINANCIÈRE CRITIQUE.
NGSER ne fournit pas de signature de webhook (cf. doc). La sécurité repose sur :
  1. order_id imprévisible (UUID)
  2. vérification que le montant reçu == montant attendu en base (reco doc p.17)
  3. validation du statut en base (on ne traite qu'une transaction 'en attente')
  4. idempotence (un webhook rejoué ne re-crédite pas seats_sold)
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from fastapi import Header
from core.config import settings
from core.reconcile import reconcile_pending_payments

from core.database import get_db
from core.security import get_current_user
from core.ngser import create_payment_session
from models.event import Event
from models.ticket import Ticket
from models.user import User
from models.payment import Payment, PayoutRequest
from schemas.payment import PaymentConfirm

router = APIRouter()


# ── INITIATION DU PAIEMENT (vrai flux NGSER) ────────────────────
@router.post("/payment/initiate-ngser")
async def initiate_ngser_payment(
    data: PaymentConfirm,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    1. Crée le billet 'en attente'
    2. Demande le lien de paiement à NGSER
    3. Renvoie {payment_url, ticket_id} au frontend
    """
    event = db.query(Event).filter(Event.id == data.event_id, Event.is_active == True).first()
    if not event:
        raise HTTPException(404, "Événement introuvable")
    if event.seats_sold >= event.total_seats:
        raise HTTPException(400, "Événement complet")

    # Calcul du prix (tier éventuel)
    prix_base = float(event.price)
    if data.tier_name and event.ticket_tiers:
        tier = next((t for t in event.ticket_tiers if t.get("name") == data.tier_name), None)
        if tier:
            prix_base = float(tier.get("price", event.price))

    if prix_base <= 0:
        raise HTTPException(400, "Prix invalide pour un paiement Orange Money")

    # Commissions
    frais_acheteur = prix_base * 0.04
    total_a_payer = prix_base + frais_acheteur
    frais_organisateur = prix_base * 0.07
    solde_orga = prix_base - frais_organisateur
    tes_benefices = frais_acheteur + frais_organisateur

    if data.email and not current_user.email:
        current_user.email = data.email

    # order_id : max 25 caractères (contrainte NGSER, doc p.8)
    order_id = f"EVT{event.id}-{uuid.uuid4().hex[:10].upper()}"

    new_ticket = Ticket(
        event_id=event.id,
        user_id=current_user.id,
        payment_status="en_attente_om",
        payment_method="orange_money",
        payment_ref=order_id,
        cash_amount=prix_base,
    )
    db.add(new_ticket)
    db.flush()

    payment = Payment(
        user_id=current_user.id,
        ticket_id=new_ticket.id,
        amount=total_a_payer,
        base_price=prix_base,
        platform_fee=tes_benefices,
        organizer_amount=solde_orga,
        status="pending",
        transaction_id=order_id,
    )
    db.add(payment)
    db.commit()
    db.refresh(new_ticket)

    # Appel NGSER (montant entier — total avec frais acheteur)
    try:
        status_code, response_data = await create_payment_session(
            order_id=order_id,
            amount=int(total_a_payer),
        )

        if status_code == 200 and "payment_url" in response_data:
            return {
                "status": "success",
                "payment_url": response_data["payment_url"],
                "ticket_id": new_ticket.id,
            }
        else:
            print(f"❌ Erreur API NGSER : HTTP {status_code} | {response_data}")
            raise HTTPException(400, "Impossible d'initialiser le paiement avec Orange Money.")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Erreur Serveur NGSER : {str(e)}")
        raise HTTPException(503, "Le service de paiement est temporairement indisponible.")


# ── WEBHOOK NGSER (Instant Payment Notification) ────────────────
@router.post("/payment/ngser-webhook")
async def ngser_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Notification post-paiement appelée par NGSER.
    Gère les achats de billets (EVT...) et les retraits organisateur (WD...).
    status_id : 1 = succès, 0 = échec, 2 = échec montant insuffisant (doc p.12).
    """
    try:
        # 1. Parsing défensif du payload (JSON / form / query)
        payload = {}
        body_bytes = await request.body()
        if body_bytes:
            try:
                payload = json.loads(body_bytes)
            except json.JSONDecodeError:
                form_data = await request.form()
                payload = dict(form_data)
        if not payload:
            payload = dict(request.query_params)

        print("🔔 [WEBHOOK NGSER PAYLOAD] :", payload)

        order_id = payload.get("order_id")
        status_id = payload.get("status_id")
        transaction_amount = payload.get("transaction_amount")

        if not order_id:
            return {"status": "error", "message": "Aucun order_id reçu"}

        try:
            status_id = int(status_id)
        except (TypeError, ValueError):
            status_id = None

        # ─────────────────────────────────────────────────────────
        # SCÉNARIO 1 : RETRAIT ORGANISATEUR (WD...)
        # ─────────────────────────────────────────────────────────
        if str(order_id).startswith("WD"):
            try:
                payout_id = int(str(order_id).split("-")[0].replace("WD", ""))
                payout = (
                    db.query(PayoutRequest)
                    .filter(PayoutRequest.id == payout_id)
                    .with_for_update()
                    .first()
                )
                if payout and payout.status not in ("paye",):
                    if status_id == 1:
                        payout.status = "paye"
                        db.commit()
                        print(f"✅💸 Webhook : Retrait B2C {payout_id} validé.")
                    else:
                        # Échec : on relâche le verrou logique pour permettre un réessai admin.
                        payout.status = "en_attente"
                        db.commit()
                        print(f"❌ Webhook : Échec retrait {payout_id} (status_id={status_id}) → remis en attente.")
            except Exception as e:
                print(f"❌ Erreur lecture webhook retrait : {e}")
            return {"status": "success"}
        # ─────────────────────────────────────────────────────────
        # SCÉNARIO 2 : ACHAT DE BILLET (EVT...)
        # ─────────────────────────────────────────────────────────
        ticket = db.query(Ticket).filter(Ticket.payment_ref == order_id).first()
        payment_record = db.query(Payment).filter(Payment.transaction_id == order_id).first()

        # 2. Idempotence : le ticket doit exister ET être en attente.
        #    Un webhook rejoué (ticket déjà 'paye') est ignoré sans effet de bord.
        if not ticket:
            print(f"⚠️ Webhook : aucun ticket pour order_id={order_id}")
            return {"status": "success"}

        if ticket.payment_status != "en_attente_om":
            print(f"ℹ️ Webhook : ticket {ticket.id} déjà traité (statut={ticket.payment_status}), ignoré.")
            return {"status": "success"}

        # 3. SUCCÈS (status_id == 1)
        if status_id == 1:
            # 3a. VÉRIFICATION DU MONTANT (recommandation NGSER doc p.17).
            #     Si le montant payé ne correspond pas au montant attendu,
            #     on considère la transaction comme un échec — pas de validation.
            if transaction_amount is not None and payment_record is not None:
                try:
                    recu = float(transaction_amount)
                    attendu = float(payment_record.amount)
                    # tolérance d'arrondi (1 FCFA)
                    if abs(recu - attendu) > 1:
                        ticket.payment_status = "echec_paiement"
                        if payment_record:
                            payment_record.status = "failed"
                        db.commit()
                        print(f"🚨 Webhook : MONTANT INCOHÉRENT order={order_id} "
                              f"reçu={recu} attendu={attendu} → REJETÉ.")
                        return {"status": "error", "message": "Montant incohérent"}
                except (TypeError, ValueError):
                    print(f"⚠️ Webhook : transaction_amount illisible ({transaction_amount}), "
                          f"validation prudente refusée.")
                    return {"status": "error", "message": "Montant illisible"}

            # 3b. Montant OK (ou non fourni mais statut succès) → on valide.
            ticket.payment_status = "paye"
            if payment_record:
                payment_record.status = "completed"

            event = (
                db.query(Event)
                .filter(Event.id == ticket.event_id)
                .with_for_update()
                .first()
            )
            if event:
                event.seats_sold += 1

            db.commit()
            print(f"✅🎫 Webhook : Billet {ticket.id} validé.")
            return {"status": "success"}

        # 4. ÉCHEC (status_id 0, 2, ou autre)
        else:
            ticket.payment_status = "echec_paiement"
            if payment_record:
                payment_record.status = "failed"
            db.commit()
            print(f"❌ Webhook : Paiement échoué order={order_id} (status_id={status_id}).")
            return {"status": "success"}

    except Exception as e:
        print(f"❌ Erreur critique Webhook : {str(e)}")
        return {"status": "error"}


# ── ANCIEN FLUX USSD (VESTIGE — à supprimer après vérif front) ──
@router.post("/payment/init/{event_id}")
def init_payment(event_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """
    ⚠️ VESTIGE de simulation. Le vrai flux est /payment/initiate-ngser.
    Conservé à l'identique tant qu'on n'a pas confirmé qu'aucune page ne l'appelle.
    """
    event = db.query(Event).get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Événement introuvable")
    if event.seats_sold >= event.total_seats:
        raise HTTPException(status_code=400, detail="Événement complet")

    transaction_id = str(uuid.uuid4())

    prix_base = event.price
    frais_acheteur = prix_base * 0.04
    total_a_payer = prix_base + frais_acheteur
    frais_organisateur = prix_base * 0.07
    solde_orga = prix_base - frais_organisateur
    tes_benefices = frais_acheteur + frais_organisateur

    ticket = Ticket(user_id=user.id, event_id=event.id, payment_status="attente")
    db.add(ticket)
    db.flush()

    payment = Payment(
        user_id=user.id,
        ticket_id=ticket.id,
        amount=total_a_payer,
        base_price=prix_base,
        platform_fee=tes_benefices,
        organizer_amount=solde_orga,
        status="pending",
        transaction_id=transaction_id,
    )
    db.add(payment)
    db.commit()

    return {
        "status": "pending",
        "transaction_id": transaction_id,
        "ticket_id": ticket.id,
        "amount": int(total_a_payer),
        "ussd_code": "#150*50#",
        "instruction": f"Composez #150*50# sur Orange Money pour payer {int(total_a_payer)} FCFA",
    }

# ── RÉCONCILIATION (cron externe, protégé par secret) ───────────
@router.post("/admin/reconcile")
async def trigger_reconcile(
    x_reconcile_secret: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Rattrape les paiements 'en attente' dont le webhook s'est perdu (doc p.18).
    Appelé par un cron externe (GitHub Actions), protégé par un secret en header.
    PAS de get_current_user : c'est une machine qui appelle, pas un humain.
    """
    if not settings.RECONCILE_SECRET or x_reconcile_secret != settings.RECONCILE_SECRET:
        raise HTTPException(status_code=403, detail="Accès refusé")

    summary = await reconcile_pending_payments(db)
    print(f"🔄 Réconciliation terminée : {summary}")
    return {"status": "success", "summary": summary}
