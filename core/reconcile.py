"""
core/reconcile.py
Réconciliation des paiements restés "en attente" (doc NGSER p.18).
Rattrape les webhooks perdus : interroge NGSER via check_payment_status
et traite le ticket / boost / pack FB comme l'aurait fait le webhook.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from core.ngser import check_payment_status
from utils.locks import lock_event_for_release
from utils.promo import release_promo_usage
from models.ticket import Ticket
from models.event import Event
from models.payment import Payment

# Fenêtre de réconciliation : on ne touche pas un ticket trop récent
# (webhook peut être en route) ni trop vieux (abandonné).
GRACE_MINUTES = 15
MAX_AGE_HOURS = 24


async def reconcile_pending_payments(db: Session) -> dict:
    """
    Parcourt les tickets / boosts / packs FB 'en_attente_om' dans la fenêtre
    [15min .. 24h], vérifie leur statut réel chez NGSER, et les traite via
    la logique unifiée process_ngser_response (même code que le webhook).
    
    Retourne un résumé { checked, validated, failed, still_pending, errors,
                         boosts_processed, fbpacks_processed }.
    """
    from core.payment_processing import process_ngser_response
    from models.promotion import BoostOrder, FbAdPackage

    now = datetime.utcnow()
    floor_recent = now - timedelta(minutes=GRACE_MINUTES)
    floor_old = now - timedelta(hours=MAX_AGE_HOURS)

    summary = {
        "checked": 0, "validated": 0, "failed": 0,
        "still_pending": 0, "errors": 0,
        "boosts_processed": 0, "fbpacks_processed": 0,
    }

    # ─── 1. Tickets en attente ──────────────────────────────────────
    pending_tickets = (
        db.query(Ticket)
        .filter(
            Ticket.payment_status == "en_attente_om",
            Ticket.purchased_at <= floor_recent,
            Ticket.purchased_at >= floor_old,
        )
        .all()
    )

    for ticket in pending_tickets:
        order_id = ticket.payment_ref
        if not order_id:
            continue

        summary["checked"] += 1

        try:
            http_code, data = await check_payment_status(order_id)
        except Exception as e:
            print(f"❌ Réconciliation ticket : erreur check {order_id} : {e}")
            summary["errors"] += 1
            continue

        code = str(data.get("code", ""))
        inner = data.get("data") or {}
        transaction_amount = inner.get("transaction_amount")

        result = process_ngser_response(
            db=db,
            order_id=order_id,
            code=code,
            transaction_amount=transaction_amount,
        )

        action = result.get("action")
        if action == "validated":
            summary["validated"] += 1
        elif action in ("failed", "rejected_amount"):
            summary["failed"] += 1
        elif action == "pending":
            summary["still_pending"] += 1
        # "skipped" / "no_ticket" → idempotence, on ne compte pas

    # ─── 2. Boosts en attente (Chantier 17) ─────────────────────────
    pending_boosts = (
        db.query(BoostOrder)
        .filter(
            BoostOrder.status == "en_attente_om",
            BoostOrder.payment_ref.isnot(None),
            BoostOrder.created_at <= floor_recent,
            BoostOrder.created_at >= floor_old,
        )
        .limit(100)
        .all()
    )

    for boost in pending_boosts:
        try:
            http_code, data = await check_payment_status(boost.payment_ref)
            code = str(data.get("code", ""))
            inner = data.get("data") or {}
            transaction_amount = inner.get("transaction_amount")
            
            process_ngser_response(
                db=db,
                order_id=boost.payment_ref,
                code=code,
                transaction_amount=transaction_amount,
            )
            summary["boosts_processed"] += 1
        except Exception as e:
            print(f"❌ Réconciliation boost #{boost.id} : {e}")
            summary["errors"] += 1

    # ─── 3. Packs FB en attente (Chantier 17) ───────────────────────
    pending_packs = (
        db.query(FbAdPackage)
        .filter(
            FbAdPackage.status == "en_attente_om",
            FbAdPackage.payment_ref.isnot(None),
            FbAdPackage.created_at <= floor_recent,
            FbAdPackage.created_at >= floor_old,
        )
        .limit(100)
        .all()
    )

    for pack in pending_packs:
        try:
            http_code, data = await check_payment_status(pack.payment_ref)
            code = str(data.get("code", ""))
            inner = data.get("data") or {}
            transaction_amount = inner.get("transaction_amount")
            
            process_ngser_response(
                db=db,
                order_id=pack.payment_ref,
                code=code,
                transaction_amount=transaction_amount,
            )
            summary["fbpacks_processed"] += 1
        except Exception as e:
            print(f"❌ Réconciliation FB pack #{pack.id} : {e}")
            summary["errors"] += 1

    print(f"🔄 Réconciliation terminée : {summary}")
    return summary


async def expire_old_orphans(db: Session, hours: int = 24) -> dict:
    """
    Purge les tickets / boosts / packs FB 'en_attente_om' trop vieux.
    On NE LES INTERROGE PLUS chez NGSER (économie d'appels), on libère direct.

    Ces orphelins existent typiquement parce que :
      - L'utilisateur a abandonné après l'initiate sans payer
      - Le webhook ET les retries de réconciliation ont tous échoué pendant 24h+

    Retourne { expired, boosts_expired, fbpacks_expired, errors }.
    """
    from models.promotion import BoostOrder, FbAdPackage

    floor = datetime.utcnow() - timedelta(hours=hours)

    summary = {"expired": 0, "boosts_expired": 0, "fbpacks_expired": 0, "errors": 0}

    # ─── 1. Tickets orphelins ───────────────────────────────────────
    orphan_tickets = (
        db.query(Ticket)
        .filter(
            Ticket.payment_status == "en_attente_om",
            Ticket.purchased_at < floor,
        )
        .all()
    )

    for ticket in orphan_tickets:
        try:
            payment_record = (
                db.query(Payment)
                .filter(Payment.transaction_id == ticket.payment_ref)
                .first()
            )
            ticket.payment_status = "expire_abandonne"
            if payment_record:
                payment_record.status = "expired"
            ev = lock_event_for_release(db, ticket.event_id)
            if ev:
                ev.seats_sold = max(0, ev.seats_sold - 1)
            release_promo_usage(db, ticket.id)  # Chantier 19.A
            db.commit()
            summary["expired"] += 1
            print(f"🧹 Orphelin ticket libéré : #{ticket.id} (créé {ticket.purchased_at}).")
        except Exception as e:
            db.rollback()
            print(f"❌ Erreur purge ticket #{ticket.id} : {e}")
            summary["errors"] += 1

    # ─── 2. Boosts orphelins (Chantier 17) ──────────────────────────
    orphan_boosts = (
        db.query(BoostOrder)
        .filter(
            BoostOrder.status == "en_attente_om",
            BoostOrder.created_at < floor,
        )
        .all()
    )

    for boost in orphan_boosts:
        try:
            boost.status = "expire_abandonne"
            db.commit()
            summary["boosts_expired"] += 1
            print(f"🧹 Orphelin boost expiré : #{boost.id}")
        except Exception as e:
            db.rollback()
            print(f"❌ Erreur purge boost #{boost.id} : {e}")
            summary["errors"] += 1

    # ─── 3. Packs FB orphelins (Chantier 17) ────────────────────────
    orphan_packs = (
        db.query(FbAdPackage)
        .filter(
            FbAdPackage.status == "en_attente_om",
            FbAdPackage.created_at < floor,
        )
        .all()
    )

    for pack in orphan_packs:
        try:
            pack.status = "expire_abandonne"
            db.commit()
            summary["fbpacks_expired"] += 1
            print(f"🧹 Orphelin FB pack expiré : #{pack.id}")
        except Exception as e:
            db.rollback()
            print(f"❌ Erreur purge FB pack #{pack.id} : {e}")
            summary["errors"] += 1

    return summary
