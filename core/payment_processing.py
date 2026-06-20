"""
core/payment_processing.py
Traitement unifié des réponses NGSER (paiement billet).
Source unique de vérité pour : webhook (validation directe) et cron (rattrapage).
"""
from sqlalchemy.orm import Session

from utils.locks import lock_event_for_release
from models.ticket import Ticket
from models.payment import Payment
from models.promotion import BoostOrder, FbAdPackage
from datetime import timedelta

def _process_ticket(db: Session, order_id: str, code: int,
                          transaction_amount: Optional[int] = None) -> dict:
    """
    Logique unifiée de traitement d'une réponse NGSER (webhook ou cron).
    Source unique de vérité. Idempotent.
    
    Dispatch selon préfixe order_id :
      EVT-... → ticket (achat billet)
      BST-... → boost organisateur
      FBP-... → pack Facebook Ads
    """
    if not order_id:
        return {"status": "no_order_id"}

    # Dispatch par préfixe
    if order_id.startswith("BST-"):
        return _process_boost(db, order_id, code, transaction_amount)
    if order_id.startswith("FBP-"):
        return _process_fbpack(db, order_id, code, transaction_amount)
    
    # Par défaut : achat ticket (préfixe EVT- ou sans préfixe pour legacy)
    return _process_ticket(db, order_id, code, transaction_amount)
                              
    ticket = (
        db.query(Ticket)
        .filter(Ticket.payment_ref == order_id)
        .with_for_update()
        .first()
    )
    if not ticket:
        return {"action": "no_ticket"}
    
    # Idempotence : on ne retouche pas un ticket déjà résolu
    if ticket.payment_status != "en_attente_om":
        return {"action": "skipped", "current_status": ticket.payment_status}
    
    payment_record = db.query(Payment).filter(Payment.transaction_id == order_id).first()
    
    # SUCCESS
    if code == "1":
        # Vérification du montant si on en a un (reco doc p.17)
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
                    db.commit()
                    print(f"🚨 process_ngser : montant incohérent {order_id} reçu={recu} attendu={attendu} → rejeté + place libérée.")
                    return {"action": "rejected_amount", "received": recu, "expected": attendu}
            except (TypeError, ValueError):
                ticket.payment_status = "echec_paiement"
                if payment_record:
                    payment_record.status = "failed"
                ev = lock_event_for_release(db, ticket.event_id)
                if ev:
                    ev.seats_sold = max(0, ev.seats_sold - 1)
                db.commit()
                print(f"⚠️ process_ngser : montant illisible ({transaction_amount}) → place libérée.")
                return {"action": "rejected_amount", "reason": "unreadable"}
        
        # Montant OK (ou non vérifiable) → validation
        ticket.payment_status = "paye"
        if payment_record:
            payment_record.status = "completed"
        db.commit()
        print(f"✅ process_ngser : billet {ticket.id} validé ({order_id}) — place déjà comptée.")
        return {"action": "validated", "ticket_id": ticket.id}
    
    # FAIL (codes "0", "2", "4", "5", "6")
    if code in ("0", "2", "4", "5", "6"):
        ticket.payment_status = "echec_paiement"
        if payment_record:
            payment_record.status = "failed"
        ev = lock_event_for_release(db, ticket.event_id)
        if ev:
            ev.seats_sold = max(0, ev.seats_sold - 1)
        db.commit()
        print(f"❌ process_ngser : paiement échoué {order_id} (code={code}) → place libérée.")
        return {"action": "failed", "code": code}
    
    # PENDING (code "3") ou inconnu → on attend
    return {"action": "pending", "code": code}

# ─── Branche BOOST (Chantier 17) ──────────────────────────────────────

def _process_boost(db: Session, order_id: str, code: int,
                  transaction_amount: Optional[int] = None) -> dict:
    """
    Traite la réponse NGSER pour un boost organisateur.
    Code NGSER : 1=success, 0/2/4/5/6=fail, 3=pending
    """
    boost = (
        db.query(BoostOrder)
        .filter(BoostOrder.payment_ref == order_id)
        .with_for_update()
        .first()
    )
    if not boost:
        return {"status": "not_found", "order_id": order_id, "type": "boost"}

    # Idempotence : si déjà traité, on ne refait rien
    if boost.status != "en_attente_om":
        return {"status": "already_processed", "boost_id": boost.id,
                "current_status": boost.status}

    # Code NGSER pending → on laisse en attente
    if code == 3:
        return {"status": "pending", "boost_id": boost.id}

    # Code NGSER fail
    if code in (0, 2, 4, 5, 6):
        boost.status = "echec_paiement"
        db.commit()
        return {"status": "failed", "boost_id": boost.id, "ngser_code": code}

    # Code NGSER success (1)
    if code == 1:
        # Vérif montant (anti-fraude)
        if transaction_amount is not None and transaction_amount < boost.amount_fcfa:
            # Montant inférieur attendu → on rejette
            boost.status = "echec_paiement"
            db.commit()
            return {"status": "amount_mismatch", "boost_id": boost.id,
                    "expected": boost.amount_fcfa, "received": transaction_amount}

        # Activation du boost
        now = datetime.utcnow()
        boost.status = "actif"
        boost.starts_at = now
        boost.ends_at = now + timedelta(days=boost.duration_days)
        db.commit()
        return {"status": "activated", "boost_id": boost.id,
                "starts_at": boost.starts_at.isoformat(),
                "ends_at": boost.ends_at.isoformat()}

    return {"status": "unknown_code", "boost_id": boost.id, "ngser_code": code}


# ─── Branche FBPACK (Chantier 17) ─────────────────────────────────────

def _process_fbpack(db: Session, order_id: str, code: int,
                   transaction_amount: Optional[int] = None) -> dict:
    """
    Traite la réponse NGSER pour un Pack Facebook Ads.
    Après paiement : statut → 'paye_attente_cm' (le CM doit lancer la campagne).
    """
    pack = (
        db.query(FbAdPackage)
        .filter(FbAdPackage.payment_ref == order_id)
        .with_for_update()
        .first()
    )
    if not pack:
        return {"status": "not_found", "order_id": order_id, "type": "fbpack"}

    if pack.status != "en_attente_om":
        return {"status": "already_processed", "fbpack_id": pack.id,
                "current_status": pack.status}

    if code == 3:
        return {"status": "pending", "fbpack_id": pack.id}

    if code in (0, 2, 4, 5, 6):
        pack.status = "echec_paiement"
        db.commit()
        return {"status": "failed", "fbpack_id": pack.id, "ngser_code": code}

    if code == 1:
        if transaction_amount is not None and transaction_amount < pack.total_paid_fcfa:
            pack.status = "echec_paiement"
            db.commit()
            return {"status": "amount_mismatch", "fbpack_id": pack.id,
                    "expected": pack.total_paid_fcfa, "received": transaction_amount}

        # Paiement OK → en attente que le CM traite
        pack.status = "paye_attente_cm"
        db.commit()
        return {"status": "paid_pending_cm", "fbpack_id": pack.id}

    return {"status": "unknown_code", "fbpack_id": pack.id, "ngser_code": code}
