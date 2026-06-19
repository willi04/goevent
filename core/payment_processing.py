"""
core/payment_processing.py
Traitement unifié des réponses NGSER (paiement billet).
Source unique de vérité pour : webhook (validation directe) et cron (rattrapage).
"""
from sqlalchemy.orm import Session

from utils.locks import lock_event_for_release
from models.ticket import Ticket
from models.payment import Payment


def process_ngser_response(
    db: Session,
    order_id: str,
    code: str,
    transaction_amount=None,
) -> dict:
    """
    Applique la décision finale sur un ticket selon le code NGSER officiel.
    
    Codes NGSER (doc p.16) :
      "1" = SUCCESS    → ticket validé (avec check montant si fourni)
      "0" = FAIL (rejet)
      "2" = FAIL (solde insuffisant)
      "3" = PENDING    → on ne fait rien, retry plus tard
      "4" = NOT EXIST  → libération place
      "5" = FAIL (timeout)
      "6" = FAIL (inconnu)
    
    Idempotent : si le ticket est déjà traité (paye / echec / etc),
    retourne {"action": "skipped"} sans rien modifier.
    
    Retourne un dict avec la clé "action" :
      "validated"      → ticket passé à "paye"
      "rejected_amount" → montant incohérent, place libérée
      "failed"          → échec NGSER, place libérée
      "pending"         → NGSER toujours en attente, rien fait
      "skipped"         → ticket déjà traité (idempotence)
      "no_ticket"       → ticket introuvable
    """
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
