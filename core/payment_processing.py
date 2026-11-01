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
from models.payment import Payment, PayoutRequest
from models.promotion import BoostOrder, FbAdPackage
from models.user import User
from models.event import Event


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

    try:
        if order_id.startswith("WD"):
            return _process_payout(db, order_id, code, transaction_amount)
        if order_id.startswith("BST-"):
            return _process_boost(db, order_id, code, transaction_amount)
        if order_id.startswith("FBP-"):
            return _process_fbpack(db, order_id, code, transaction_amount)
        return _process_ticket(db, order_id, code, transaction_amount)
    finally:
        # Les branches pending/skipped/ambiguous retournent SANS commit alors
        # qu'elles ont pris un SELECT ... FOR UPDATE. Dans la boucle reconcile
        # (async, plusieurs appels NGSER de 15s), ces verrous restaient tenus
        # jusqu'à la fin du cron → webhook du même billet bloqué. Toute mutation
        # légitime a déjà été commitée par la branche : rollback = libération seule.
        if db.in_transaction():
            db.rollback()


def _process_payout(db, order_id, code, transaction_amount=None):
    """
    Retrait organisateur (B2C). Idempotent (skip si déjà 'paye').
    Sur montant incohérent : ne touche PAS au statut (ni paye ni en_attente) —
    c'est de l'argent sortant, un écart suspect doit être investigué par un humain,
    pas tranché automatiquement dans un sens ou l'autre.
    """
    try:
        payout_id = int(str(order_id).split("-")[0].replace("WD", ""))
    except (TypeError, ValueError):
        return {"action": "invalid_order_id", "order_id": order_id}

    payout = (
        db.query(PayoutRequest)
        .filter(PayoutRequest.id == payout_id)
        .with_for_update()
        .first()
    )
    if not payout:
        return {"action": "no_payout", "payout_id": payout_id}

    if payout.status == "paye":
        return {"action": "skipped", "payout_id": payout.id, "current_status": payout.status}

    if code == "1":
        if transaction_amount is not None:
            try:
                recu = float(transaction_amount)
                attendu = float(payout.amount)
                if abs(recu - attendu) > 1:
                    print(f"🚨 process_ngser PAYOUT : montant incohérent {order_id} reçu={recu} attendu={attendu}.")
                    return {"action": "rejected_amount", "payout_id": payout.id,
                            "received": recu, "expected": attendu}
            except (TypeError, ValueError):
                return {"action": "rejected_amount", "payout_id": payout.id, "reason": "unreadable"}

        payout.status = "paye"
        db.commit()
        print(f"✅💸 process_ngser PAYOUT : retrait {payout.id} validé.")
        return {"action": "validated", "payout_id": payout.id}

    if code in ("0", "2"):
        # Échec EXPLICITE (rejeté / solde insuffisant) : aucun argent sorti.
        payout.status = "en_attente"
        db.commit()
        print(f"❌ process_ngser PAYOUT : échec retrait {payout.id} (code={code}) → remis en attente.")
        return {"action": "failed", "payout_id": payout.id, "code": code}

    if code in ("4", "5", "6"):
        # NOT EXIST / TIMEOUT / INCONNU : issue indéterminée côté argent SORTANT.
        # Remettre en_attente ouvrait la porte à un 2e virement (nouveau WD order_id).
        # On laisse en_cours : investigation humaine (cf. docstring).
        print(f"🚨 process_ngser PAYOUT : statut ambigu retrait {payout.id} (code={code}) → reste en_cours, vérif manuelle.")
        return {"action": "ambiguous", "payout_id": payout.id, "code": code}

    return {"action": "pending", "payout_id": payout.id, "code": code}

def _process_ticket(db, order_id, code, transaction_amount=None):
    """Commande de 1 à N billets (lot 5.5) : tous les billets du même order_id
    sont validés ou libérés ENSEMBLE, contre UN montant NGSER (somme des Payment)."""
    tickets = (
        db.query(Ticket)
        .filter(Ticket.payment_ref == order_id)
        .order_by(Ticket.id)
        .with_for_update()
        .all()
    )
    if not tickets:
        return {"action": "no_ticket"}

    pending = [t for t in tickets if t.payment_status == "en_attente_om"]
    if not pending:
        return {"action": "skipped", "current_status": tickets[0].payment_status}

    ids = [t.id for t in pending]
    payments = db.query(Payment).filter(Payment.transaction_id == order_id).all()
    by_ticket = {p.ticket_id: p for p in payments}

    def release(status, failure_reason=None):
        for t in pending:
            t.payment_status = status
            if failure_reason:
                t.failure_reason = failure_reason
            p = by_ticket.get(t.id)
            if p:
                p.status = "failed"
            release_promo_usage(db, t.id)
        ev = lock_event_for_release(db, pending[0].event_id)
        if ev:
            ev.seats_sold = max(0, ev.seats_sold - len(pending))

    if code == "1":
        if transaction_amount is not None and payments:
            try:
                recu = float(transaction_amount)
                attendu = float(sum(float(p.amount or 0) for p in payments))
                if abs(recu - attendu) > 1:
                    release("echec_paiement")
                    db.commit()
                    print(f"🚨 process_ngser : montant incohérent {order_id} reçu={recu} attendu={attendu}.")
                    return {"action": "rejected_amount", "received": recu, "expected": attendu}
            except (TypeError, ValueError):
                release("echec_paiement")
                db.commit()
                return {"action": "rejected_amount", "reason": "unreadable"}

        for t in pending:
            t.payment_status = "paye"
            p = by_ticket.get(t.id)
            if p:
                p.status = "completed"
            confirm_promo_usage(db, t.id)
        db.commit()
        print(f"✅ process_ngser : {len(pending)} billet(s) validé(s) ({order_id}).")

        try:
            from core.email import envoyer_billet_email
            user = db.query(User).filter(User.id == pending[0].user_id).first()
            event = db.query(Event).filter(Event.id == pending[0].event_id).first()
            if user and user.email and event:
                amount = sum(float(p.amount or 0) for p in payments)
                envoyer_billet_email(
                    email_client=user.email,
                    nom_client=user.full_name or "Client",
                    ticket=pending[0],
                    event=event,
                    amount=int(amount),
                    tickets=pending,
                )
        except Exception as e:
            print(f"⚠️ Envoi email billet échoué (non bloquant) : {e}")

        return {"action": "validated", "ticket_id": ids[0], "ticket_ids": ids}

    if code in ("0", "2", "4", "5", "6"):
        reason = "insufficient_balance" if code == "2" else "cancelled" if code == "0" else "rejected"
        release("echec_paiement", reason)
        db.commit()
        print(f"❌ process_ngser : paiement échoué {order_id} (code={code}, {len(pending)} billet(s)).")
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
