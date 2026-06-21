"""
core/payment_processing.py
Traitement unifié des réponses NGSER.
Source unique de vérité pour : webhook (validation directe) et cron (rattrapage).

Dispatch par préfixe order_id :
  EVT... ou legacy → ticket (achat billet)
  BST-...          → boost organisateur (Chantier 17)
  FBP-...          → pack Facebook Ads (Chantier 17)
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from utils.locks import lock_event_for_release
from utils.promo import confirm_promo_usage, release_promo_usage
from models.ticket import Ticket
from models.payment import Payment
from models.promotion import BoostOrder, FbAdPackage


def process_ngser_response(
    db: Session,
    order_id: str,
    code: str,
    transaction_amount: Optional[int] = None,
) -> dict:
    """
    Logique unifiée. Idempotent. Dispatch par préfixe order_id.
    Codes NGSER : "1"=SUCCESS, "0"/"2"/"4"/"5"/"6"=FAIL, "3"=PENDING.
    """
    if not order_id:
        return {"action": "no_order_id"}

    if order_id.startswith("BST-"):
        return _process_boost(db, order_id, code, transaction_amount)
    if order_id.startswith("FBP-"):
        return _process_fbpack(db, order_id, code, transaction_amount)

    return _process_ticket(db, order_id, code, transaction_amount)


def _process_ticket(db, order_id, code, transaction_amount=None):
    ticket = (
        db.query(Ticket)
        .filter(Ticket.payment_ref == order_id)
        .with_for_update()
        .first()
    )
    if not ticket:
        return {"action": "no_ticket"}

    if ticket.payment_status != "en_attente_om":
        return {"action": "skipped", "current_status": ticket.payment_status}

    payment_record = db.query(Payment).filter(Payment.transaction_id == order_id).first()

    if code == "1":
        if transaction_amount is not None and payment_record is not None:
            try:
                recu = float(transaction_amount)
                attendu = float(payment_record.amount)
                if abs(recu - attendu) > 1:
                    ticket.payment_status = "echec_paiement"
                    payment_record.status = "failed"
                    ev = lock_event_for_release(db, ticket.event_id)
                    if ev:
                        ev.seats_sold = max(0, ev.seats_sold - 1)
                    release_promo_usage(db, ticket.id)  # Chantier 19.A
                    db.commit()
                    print(f"🚨 process_ngser : montant incohérent {order_id} reçu={recu} attendu={attendu}.")
                    return {"action": "rejected_amount", "received": recu, "expected": attendu}
            except (TypeError, ValueError):
                ticket.payment_status = "echec_paiement"
                if payment_record:
                    payment_record.status = "failed"
                ev = lock_event_for_release(db, ticket.event_id)
                if ev:
                    ev.seats_sold = max(0, ev.seats_sold - 1)
                release_promo_usage(db, ticket.id)  # Chantier 19.A
                db.commit()
                return {"action": "rejected_amount", "reason": "unreadable"}

        ticket.payment_status = "paye"
        if payment_record:
            payment_record.status = "completed"
        # Confirmation promo (Chantier 19.A)
        confirm_promo_usage(db, ticket.id)
        db.commit()
        print(f"✅ process_ngser : billet {ticket.id} validé ({order_id}).")
        return {"action": "validated", "ticket_id": ticket.id}

    if code in ("0", "2", "4", "5", "6"):
        ticket.payment_status = "echec_paiement"
        if payment_record:
            payment_record.status = "failed"
        ev = lock_event_for_release(db, ticket.event_id)
        if ev:
            ev.seats_sold = max(0, ev.seats_sold - 1)
        release_promo_usage(db, ticket.id)  # Chantier 19.A
        db.commit()
        print(f"❌ process_ngser : paiement échoué {order_id} (code={code}).")
        return {"action": "failed", "code": code}

    return {"action": "pending", "code": code}


def _process_boost(db, order_id, code, transaction_amount=None):
    boost = (
        db.query(BoostOrder)
        .filter(BoostOrder.payment_ref == order_id)
        .with_for_update()
        .first()
    )
    if not boost:
        return {"action": "no_boost", "order_id": order_id}

    if boost.status != "en_attente_om":
        return {"action": "skipped", "boost_id": boost.id, "current_status": boost.status}

    if code == "3":
        return {"action": "pending", "boost_id": boost.id}

    if code in ("0", "2", "4", "5", "6"):
        boost.status = "echec_paiement"
        db.commit()
        print(f"❌ process_ngser BOOST : échec {order_id} (code={code}).")
        return {"action": "failed", "boost_id": boost.id, "code": code}

    if code == "1":
        if transaction_amount is not None:
            try:
                recu = float(transaction_amount)
                attendu = float(boost.amount_fcfa)
                if abs(recu - attendu) > 1:
                    boost.status = "echec_paiement"
                    db.commit()
                    print(f"🚨 BOOST : montant incohérent {order_id} reçu={recu} attendu={attendu}.")
                    return {"action": "rejected_amount", "boost_id": boost.id,
                            "received": recu, "expected": attendu}
            except (TypeError, ValueError):
                boost.status = "echec_paiement"
                db.commit()
                return {"action": "rejected_amount", "boost_id": boost.id, "reason": "unreadable"}

        now = datetime.utcnow()
        boost.status = "actif"
        boost.starts_at = now
        boost.ends_at = now + timedelta(days=boost.duration_days)
        db.commit()
        print(f"✅ process_ngser BOOST : boost {boost.id} activé jusqu'au {boost.ends_at}.")
        return {"action": "validated", "boost_id": boost.id}

    return {"action": "pending", "boost_id": boost.id, "code": code}


def _process_fbpack(db, order_id, code, transaction_amount=None):
    pack = (
        db.query(FbAdPackage)
        .filter(FbAdPackage.payment_ref == order_id)
        .with_for_update()
        .first()
    )
    if not pack:
        return {"action": "no_fbpack", "order_id": order_id}

    if pack.status != "en_attente_om":
        return {"action": "skipped", "fbpack_id": pack.id, "current_status": pack.status}

    if code == "3":
        return {"action": "pending", "fbpack_id": pack.id}

    if code in ("0", "2", "4", "5", "6"):
        pack.status = "echec_paiement"
        db.commit()
        print(f"❌ process_ngser FBPACK : échec {order_id} (code={code}).")
        return {"action": "failed", "fbpack_id": pack.id, "code": code}

    if code == "1":
        if transaction_amount is not None:
            try:
                recu = float(transaction_amount)
                attendu = float(pack.total_paid_fcfa)
                if abs(recu - attendu) > 1:
                    pack.status = "echec_paiement"
                    db.commit()
                    return {"action": "rejected_amount", "fbpack_id": pack.id,
                            "received": recu, "expected": attendu}
            except (TypeError, ValueError):
                pack.status = "echec_paiement"
                db.commit()
                return {"action": "rejected_amount", "fbpack_id": pack.id, "reason": "unreadable"}

        pack.status = "paye_attente_cm"
        db.commit()
        print(f"✅ process_ngser FBPACK : pack {pack.id} payé, en attente du CM.")
        return {"action": "validated", "fbpack_id": pack.id}

    return {"action": "pending", "fbpack_id": pack.id, "code": code}
