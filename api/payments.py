
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from fastapi import Header
from core.config import settings, COMMISSION_BUYER_RATE, COMMISSION_ORGANIZER_RATE
from core.reconcile import reconcile_pending_payments, expire_old_orphans
from core.limiter import limiter

from utils.locks import lock_event_for_booking, lock_event_for_release
from utils.promo import lock_promo_for_use, compute_discount, release_promo_usage
from core.orders import MAX_PER_ACCOUNT, assert_account_limit, assert_capacity, unit_total
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
@limiter.limit("15/minute")
async def initiate_ngser_payment(
    request: Request,
    data: PaymentConfirm,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Commande de N billets (1 à MAX_PER_ORDER, lot 5.5), UN seul paiement NGSER.
    1. Verrou pessimiste + check quota N places + plafond par compte
    2. N tickets 'en_attente_om' + N payments 'pending', même order_id (payment_ref)
    3. PRÉ-RÉSERVATION : seats_sold += N avant l'appel NGSER
    4. Si NGSER échoue → libère les N places
    Abandon sans payer : places libérées par la réconciliation (> 15 min / 24 h).
    """
    qty = data.quantity
    # 1. Verrou + check quota atomique (P0 anti-survente)
    event = lock_event_for_booking(db, data.event_id)
    assert_capacity(event, qty)
    assert_account_limit(db, current_user.id, event.id, qty, MAX_PER_ACCOUNT)

    # 2. Prix (tier éventuel ; un tarif inconnu n'est plus remplacé en silence)
    prix_base = float(event.price)
    tier_name = None
    if data.tier_name and event.ticket_tiers:
        tier = next((t for t in event.ticket_tiers if t.get("name") == data.tier_name), None)
        if not tier:
            raise HTTPException(400, "Catégorie de billet invalide")
        try:
            prix_base = float(tier.get("price", event.price))
        except (ValueError, TypeError):
            raise HTTPException(400, "Catégorie de billet invalide")
        tier_name = tier.get("name")

    if prix_base <= 0:
        raise HTTPException(400, "Prix invalide pour un paiement Orange Money")

    # 2.bis CODE PROMO (Chantier 19.A) : une utilisation par billet de la commande.
    # La promo s'applique UNIQUEMENT sur prix_base, jamais sur les frais GoEvent.
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

        # Promo -100 % : NGSER refuse un montant 0 → billet délivré par
        # POST /tickets/reserve-free (checkout.html bascule déjà dessus).
        if prix_base <= 0:
            raise HTTPException(
                400,
                "Le code promo rend ce billet gratuit : rechargez la page pour l'obtenir directement."
            )

    # Montants PAR BILLET en FCFA entiers : total commande = qty × montant_billet,
    # identique à l'affichage (checkout.html) et à la somme des Payment.amount.
    montant_billet = unit_total(prix_base, COMMISSION_BUYER_RATE)
    frais_acheteur = montant_billet - prix_base
    frais_organisateur = prix_base * COMMISSION_ORGANIZER_RATE
    solde_orga = prix_base - frais_organisateur
    tes_benefices = frais_acheteur + frais_organisateur
    total_commande = montant_billet * qty

    if data.email and not current_user.email:
        current_user.email = data.email

    order_id = f"EVT{event.id}-{uuid.uuid4().hex[:10].upper()}"

    tickets = []
    for _ in range(qty):
        t = Ticket(
            event_id=event.id,
            user_id=current_user.id,
            payment_status="en_attente_om",
            payment_method="orange_money",
            payment_ref=order_id,
            cash_amount=prix_base,
            tier_name=tier_name,
        )
        db.add(t)
        tickets.append(t)
    db.flush()

    for t in tickets:
        db.add(Payment(
            user_id=current_user.id,
            ticket_id=t.id,
            amount=montant_billet,
            base_price=prix_base,
            platform_fee=tes_benefices,
            organizer_amount=solde_orga,
            status="pending",
            transaction_id=order_id,
        ))
        # PromoUsage 'reserved' par billet + incrément used_count (Chantier 19.A)
        # Le verrou with_for_update sur promo_obj est encore actif jusqu'au commit.
        if promo_obj:
            db.add(PromoUsage(
                promo_id=promo_obj.id,
                user_id=current_user.id,
                ticket_id=t.id,
                discount_fcfa=discount_fcfa,
                status="reserved",
            ))
    if promo_obj:
        promo_obj.used_count += qty

    # 3. PRÉ-RÉSERVATION : les N places sont comptées AVANT l'appel NGSER.
    event.seats_sold += qty
    db.commit()
    ticket_ids = [t.id for t in tickets]

    # 4. Appel NGSER (verrou relâché par le commit)
    try:
        status_code, response_data = await create_payment_session(
            order_id=order_id,
            amount=int(total_commande),
        )
        if status_code == 200 and "payment_url" in response_data:
            return {
                "status": "success",
                "payment_url": response_data["payment_url"],
                "ticket_id": ticket_ids[0],
                "ticket_ids": ticket_ids,
                "order_ref": order_id,
                "quantity": qty,
                "amount": int(total_commande),
            }
        else:
            print(f"❌ Erreur API NGSER : HTTP {status_code} | {response_data}")
            _libere_place_init_echouee(db, ticket_ids, event.id)
            raise HTTPException(400, "Impossible d'initialiser le paiement avec Orange Money.")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Erreur Serveur NGSER : {str(e)}")
        _libere_place_init_echouee(db, ticket_ids, event.id)
        raise HTTPException(503, "Le service de paiement est temporairement indisponible.")


def _libere_place_init_echouee(db: Session, ticket_ids, event_id: int):
    """Libère la pré-réservation (N places) si l'appel NGSER initial a échoué."""
    if isinstance(ticket_ids, int):
        ticket_ids = [ticket_ids]
    try:
        # Même ordre de verrous que process_ngser_response : billets, puis événement
        tickets = (db.query(Ticket).filter(Ticket.id.in_(ticket_ids), Ticket.payment_status == "en_attente_om")
                   .order_by(Ticket.id).with_for_update().all())
        event = lock_event_for_release(db, event_id) if tickets else None
        for ticket in tickets:
            ticket.payment_status = "echec_init_ngser"
            release_promo_usage(db, ticket.id)   # Chantier 19.A
        if event and tickets:
            event.seats_sold = max(0, event.seats_sold - len(tickets))
        db.query(Payment).filter(Payment.ticket_id.in_(ticket_ids), Payment.status == "pending").update(
            {"status": "failed"}, synchronize_session=False)
        db.commit()
    except Exception as e:
        print(f"⚠️ Erreur libération place après échec init NGSER : {e}")
        db.rollback()

@router.post("/payment/ngser-webhook")
@limiter.limit("60/minute")
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
@limiter.limit("20/minute")
async def trigger_reconcile(
    request: Request,
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
    # expire_old_orphans n'était appelé NULLE PART : tout checkout OM abandonné
    # >24h gardait sa place pré-réservée À VIE (seats_sold jamais décrémenté).
    summary["orphans"] = await expire_old_orphans(db)
    # Données personnelles : listes d'invités anonymisées 30 j après l'événement
    from core.invitations import purge_due
    summary["invitations_anonymized"] = purge_due(db)
    # Prospects : devis non convertis anonymisés 12 mois après leur date de validité (lot 4.5)
    from core.quotes import purge_due as purge_quotes
    summary["quotes_anonymized"] = purge_quotes(db)
    print(f"🔄 Réconciliation terminée : {summary}")
    return {"status": "success", "summary": summary}
