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

        # SUCCESS (code "1") → on valide. La place a DÉJÀ été comptée à l'initiate
        # (pré-réservation P0), donc PAS d'incrément ici. Sinon double comptage.
        if code == "1":
            payment_record = db.query(Payment).filter(Payment.transaction_id == order_id).first()

            # Vérification du montant (reco doc p.17), si disponible
            inner = data.get("data") or {}
            recu = inner.get("transaction_amount")
            if recu is not None and payment_record is not None:
                try:
                    if abs(float(recu) - float(payment_record.amount)) > 1:
                        # Montant incohérent → on rejette ET on libère la place pré-réservée
                        ticket.payment_status = "echec_paiement"
                        payment_record.status = "failed"
                        ev = lock_event_for_release(db, ticket.event_id)
                        if ev:
                            ev.seats_sold = max(0, ev.seats_sold - 1)
                        db.commit()
                        print(f"🚨 Réconciliation : montant incohérent {order_id} → rejeté + place libérée.")
                        summary["failed"] += 1
                        continue
                except (TypeError, ValueError):
                    pass

            ticket.payment_status = "paye"
            if payment_record:
                payment_record.status = "completed"
            db.commit()
            print(f"✅ Réconciliation : billet {ticket.id} validé ({order_id}) — place déjà comptée.")
            summary["validated"] += 1

        # FAIL (codes "0", "2", "5", "6") ou NOT EXIST (code "4")
       # FAIL (codes "0", "2", "5", "6") ou NOT EXIST (code "4")
        # → libération de la place pré-réservée (P0)
        elif code in ("0", "2", "4", "5", "6"):
            payment_record = db.query(Payment).filter(Payment.transaction_id == order_id).first()
            ticket.payment_status = "echec_paiement"
            if payment_record:
                payment_record.status = "failed"
            ev = lock_event_for_release(db, ticket.event_id)
            if ev:
                ev.seats_sold = max(0, ev.seats_sold - 1)
            db.commit()
            print(f"❌ Réconciliation : paiement échoué {order_id} (code={code}) → place libérée.")
            summary["failed"] += 1

        # PENDING (code "3") → on laisse, prochain passage
        else:
            summary["still_pending"] += 1

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
