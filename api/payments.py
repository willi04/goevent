
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from fastapi import Header
from core.config import settings
from core.reconcile import reconcile_pending_payments

from utils.locks import lock_event_for_booking, lock_event_for_release
from utils.promo import lock_promo_for_use, compute_discount, release_promo_usage
from core.database import get_db
from core.security import get_current_user
from core.ngser import create_payment_session, check_payment_status
from models.event import Event
from models.ticket import Ticket
from models.user import User
from models.payment import Payment
from models.promo import PromoUsage
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
    1. Verrou pessimiste + check quota + PRÉ-RÉSERVATION (seats_sold += 1)
    2. Création ticket 'en_attente_om' + payment 'pending'
    3. Appel NGSER pour le lien de paiement
    4. Si NGSER échoue → libère la place
    Note : si l'utilisateur abandonne sans payer, la place reste réservée
    jusqu'au cron de réconciliation (P1) qui libère les en_attente_om > 15min.
    """
    # 1. Verrou + check quota atomique (P0 anti-survente)
    event = lock_event_for_booking(db, data.event_id)

   # 2. Prix (tier éventuel)
    prix_base = float(event.price)
    if data.tier_name and event.ticket_tiers:
        tier = next((t for t in event.ticket_tiers if t.get("name") == data.tier_name), None)
        if tier:
            prix_base = float(tier.get("price", event.price))

    if prix_base <= 0:
        raise HTTPException(400, "Prix invalide pour un paiement Orange Money")

    # 2.bis APPLICATION CODE PROMO (Chantier 19.A)
    # La promo s'applique UNIQUEMENT sur prix_base, jamais sur les frais GoEvent (4% + 7%).
    # Verrou pessimiste + revalidation atomique des 7 checks dans lock_promo_for_use.
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
        )
        discount_fcfa = compute_discount(promo_obj, prix_base)
        prix_base = max(0.0, prix_base - discount_fcfa)

        # Si la promo rend le prix=0, on refuse côté OM : NGSER ne sait pas traiter un montant 0.
        # Le fan doit basculer sur le flux cash (qui acceptera la promo et générera un billet gratuit).
        if prix_base <= 0:
            raise HTTPException(
                400,
                "Le code promo rend ce billet gratuit. Choisissez le paiement cash à l'entrée."
            )

    frais_acheteur = prix_base * 0.04
    total_a_payer = prix_base + frais_acheteur
    frais_organisateur = prix_base * 0.07
    solde_orga = prix_base - frais_organisateur
    tes_benefices = frais_acheteur + frais_organisateur

    if data.email and not current_user.email:
        current_user.email = data.email

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

    # Insertion PromoUsage en 'reserved' + incrément used_count (Chantier 19.A)
    # Le verrou with_for_update sur promo_obj (via lock_promo_for_use) est encore actif jusqu'au commit.
    if promo_obj:
        db.add(PromoUsage(
            promo_id=promo_obj.id,
            user_id=current_user.id,
            ticket_id=new_ticket.id,
            discount_fcfa=discount_fcfa,
            status="reserved",
        ))
        promo_obj.used_count += 1

    # 3. PRÉ-RÉSERVATION : la place est comptée AVANT l'appel NGSER.
    #    Le verrou pessimiste garantit l'atomicité avec d'autres workers.
    event.seats_sold += 1
    db.commit()
    db.refresh(new_ticket)

    # 4. Appel NGSER (verrou relâché par le commit)
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
            _libere_place_init_echouee(db, new_ticket.id, event.id)
            raise HTTPException(400, "Impossible d'initialiser le paiement avec Orange Money.")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Erreur Serveur NGSER : {str(e)}")
        _libere_place_init_echouee(db, new_ticket.id, event.id)
        raise HTTPException(503, "Le service de paiement est temporairement indisponible.")


def _libere_place_init_echouee(db: Session, ticket_id: int, event_id: int):
    """Libère la pré-réservation si l'appel NGSER initial a échoué."""
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if ticket and ticket.payment_status == "en_attente_om":
            ticket.payment_status = "echec_init_ngser"
            event = lock_event_for_release(db, event_id)
            if event:
                event.seats_sold = max(0, event.seats_sold - 1)
            # Libération de l'éventuelle promo associée (Chantier 19.A)
            release_promo_usage(db, ticket_id)
            db.commit()
    except Exception as e:
        print(f"⚠️ Erreur libération place après échec init NGSER : {e}")
        db.rollback()

@router.post("/payment/ngser-webhook")
async def ngser_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Notification post-paiement appelée par NGSER (sert uniquement de trigger).
    Le payload entrant n'est jamais une source de vérité : order_id est extrait,
    puis check_payment_status() interroge NGSER activement pour le vrai statut.
    Gère les achats de billets (EVT...), retraits organisateur (WD...),
    boosts (BST-...) et packs FB Ads (FBP-...) via process_ngser_response.
    Codes NGSER : "1"=succès, "0"/"2"/"4"/"5"/"6"=échec, "3"=en attente.
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
        if not order_id:
            return {"status": "error", "message": "Aucun order_id reçu"}

        # ─────────────────────────────────────────────────────────
        # SÉCURITÉ : cette route est publique et sans authentification.
        # status_id/transaction_amount du payload entrant NE SONT JAMAIS UNE SOURCE
        # DE VÉRITÉ — n'importe qui peut POST ici en se faisant passer pour NGSER.
        # order_id sert uniquement de pointeur ; le vrai statut est vérifié activement
        # auprès de NGSER (même mécanisme que le cron, cf core/reconcile.py).
        # process_ngser_response dispatche lui-même selon le préfixe order_id
        # (WD = retrait, BST-/FBP- = régie pub, sinon = ticket).
        # ─────────────────────────────────────────────────────────
        from core.payment_processing import process_ngser_response

        try:
            http_code, status_data = await check_payment_status(order_id)
        except Exception as e:
            print(f"❌ Webhook : erreur check_payment_status({order_id}) : {e}")
            return {"status": "error", "message": "Vérification NGSER indisponible"}

        verified_code = str(status_data.get("code", ""))
        verified_inner = status_data.get("data") or {}
        verified_amount = verified_inner.get("transaction_amount")

        result = process_ngser_response(
            db=db,
            order_id=order_id,
            code=verified_code,
            transaction_amount=verified_amount,
        )
        print(f"🔔 Webhook : {order_id} → {result}")
        return {"status": "success", "action": result.get("action")}

    except Exception as e:
        print(f"❌ Erreur critique Webhook : {str(e)}")
        return {"status": "error"}


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
