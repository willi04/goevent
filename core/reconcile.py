"""
core/reconcile.py
Réconciliation des paiements restés "en attente" (doc NGSER p.18).
Rattrape les webhooks perdus : interroge NGSER via check_payment_status
et traite le ticket comme l'aurait fait le webhook.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from core.ngser import check_payment_status
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
    vérifie leur statut réel chez NGSER, et les traite.
    Retourne un résumé { checked, validated, failed, still_pending, errors }.
    """
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

        # SUCCESS (code "1") → on valide comme le webhook, avec verrou anti-survente
        if code == "1":
            payment_record = db.query(Payment).filter(Payment.transaction_id == order_id).first()

            # Vérification du montant (reco doc p.17), si disponible
            inner = data.get("data") or {}
            recu = inner.get("transaction_amount")
            if recu is not None and payment_record is not None:
                try:
                    if abs(float(recu) - float(payment_record.amount)) > 1:
                        ticket.payment_status = "echec_paiement"
                        payment_record.status = "failed"
                        db.commit()
                        print(f"🚨 Réconciliation : montant incohérent {order_id} → rejeté.")
                        summary["failed"] += 1
                        continue
                except (TypeError, ValueError):
                    pass

            event = (
                db.query(Event)
                .filter(Event.id == ticket.event_id)
                .with_for_update()
                .first()
            )
            ticket.payment_status = "paye"
            if payment_record:
                payment_record.status = "completed"
            if event:
                event.seats_sold += 1
            db.commit()
            print(f"✅ Réconciliation : billet {ticket.id} validé ({order_id}).")
            summary["validated"] += 1

        # FAIL (codes "0", "2", "5", "6") ou NOT EXIST (code "4")
        elif code in ("0", "2", "4", "5", "6"):
            payment_record = db.query(Payment).filter(Payment.transaction_id == order_id).first()
            ticket.payment_status = "echec_paiement"
            if payment_record:
                payment_record.status = "failed"
            db.commit()
            print(f"❌ Réconciliation : paiement échoué {order_id} (code={code}).")
            summary["failed"] += 1

        # PENDING (code "3") → on laisse, prochain passage
        else:
            summary["still_pending"] += 1

    return summary
