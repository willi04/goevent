"""
core/orders.py — Achat de plusieurs billets en une commande (lot 5.5).

Une commande = N billets (un QR chacun) partageant le même payment_ref
(order_id NGSER) et UN seul paiement Orange Money. Un enregistrement Payment
par billet (remboursements et recettes restent calculés billet par billet).
Montant : chaque billet = int(prix + frais) ; total = N × ce montant
→ le montant affiché au fan = montant envoyé à NGSER = somme des Payment.
"""
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.event import Event
from models.ticket import Ticket

MAX_PER_ORDER = 5           # billets payants par commande
MAX_PER_ACCOUNT = 10        # billets payants par compte et par événement (anti-revente)
MAX_FREE_PER_ACCOUNT = 3    # billets gratuits par compte et par événement
HELD = ("paye", "paye_cash", "en_attente_om")   # billets qui comptent dans les plafonds


def assert_capacity(event: Event, quantity: int) -> None:
    """Sous verrou lock_event_for_booking : assez de places pour N billets."""
    left = (event.total_seats or 0) - (event.seats_sold or 0)
    if left < quantity:
        if left <= 0:
            raise HTTPException(409, "Plus de places disponibles")
        raise HTTPException(409, "Il ne reste que %d place(s) : réduisez la quantité." % left)


def assert_account_limit(db: Session, user_id: int, event_id: int, quantity: int, limit: int, statuses=HELD) -> None:
    held = db.query(func.count(Ticket.id)).filter(
        Ticket.user_id == user_id, Ticket.event_id == event_id, Ticket.payment_status.in_(statuses)).scalar() or 0
    if held + quantity > limit:
        left = max(0, limit - held)
        if not left:
            raise HTTPException(400, "Vous avez atteint la limite de %d billets pour cet événement." % limit)
        raise HTTPException(400, "Limite de %d billets par personne pour cet événement : vous pouvez encore en prendre %d." % (limit, left))


def unit_total(prix_base: float, buyer_rate: float) -> int:
    """Montant débité pour UN billet (FCFA entiers, même calcul que checkout.html)."""
    return int(prix_base + prix_base * buyer_rate)
