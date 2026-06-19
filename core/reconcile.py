"""
core/reconcile.py
Réconciliation des paiements restés "en attente" (doc NGSER p.18).
Rattrape les webhooks perdus : interroge NGSER via check_payment_status
et traite le ticket comme l'aurait fait le webhook.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from core.ngser import check_payment_status
from utils.locks import lock_event_for_release
from models.ticket import Ticket
from models.event import Event
from models.payment import Payment
# Fenêtre de réconciliation : on ne touche pas un ticket trop récent
# (webhook peut être en route) ni trop vieux (abandonné).
GRACE_MINUTES = 15
MAX_AGE_HOURS = 24


async def reconcile_pending_payments(db: Session) -> dict:
    """
    Parcourt les tickets 'en_attente_om' dans la fenêtre [15min .. 24h],
    vérifie leur statut réel chez NGSER, et les traite via la logique
    unifiée process_ngser_response (même code que le webhook).
    Retourne un résumé { checked, validated, failed, still_pending, errors }.
    """
    from core.payment_processing import process_ngser_response
    
    now = datetime.utcnow()
    floor_recent = now - timedelta(minutes=GRACE_MINUTES)
    floor_old = now - timedelta(hours=MAX_AGE_HOURS)

    pending = (
        db.query(Ticket)
        .filter(
            Ticket.payment_status == "en_attente_om",
            Ticket.purchased_at <= floor_recent,
            Ticket.purchased_at >= floor_old,
        )
        .all()
    )

    summary = {"checked": 0, "validated": 0, "failed": 0, "still_pending": 0, "errors": 0}

    for ticket in pending:
        order_id = ticket.payment_ref
        if not order_id:
            continue

        summary["checked"] += 1

        try:
            http_code, data = await check_payment_status(order_id)
        except Exception as e:
            print(f"❌ Réconciliation : erreur check {order_id} : {e}")
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
        # "skipped" / "no_ticket" → on ne compte pas (idempotence)

    return summary


    
async def expire_old_orphans(db: Session, hours: int = 24) -> dict:
    """
    Purge les tickets 'en_attente_om' trop vieux pour être encore en jeu côté NGSER
    (session de paiement Orange Money expirée depuis longtemps).
    On NE LES INTERROGE PLUS chez NGSER (économie d'appels), on les libère direct.

    Ces orphelins existent typiquement parce que :
      - Le fan a abandonné après l'initiate sans payer
      - Le webhook ET les retries de réconciliation ont tous échoué pendant 24h+

    Retourne { expired, errors }.
    """
    floor = datetime.utcnow() - timedelta(hours=hours)
    orphans = (
        db.query(Ticket)
        .filter(
            Ticket.payment_status == "en_attente_om",
            Ticket.purchased_at < floor,
        )
        .all()
    )

    summary = {"expired": 0, "errors": 0}

    for ticket in orphans:
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
            db.commit()
            summary["expired"] += 1
            print(f"🧹 Orphelin libéré : ticket {ticket.id} (créé {ticket.purchased_at}).")
        except Exception as e:
            db.rollback()
            print(f"❌ Erreur purge ticket {ticket.id} : {e}")
            summary["errors"] += 1

    return summary
