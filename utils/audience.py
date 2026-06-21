"""
utils/audience.py — Helpers pour calcul des segments audience (Chantier 19.B).

Calcule les 3 segments à la volée via SQL agrégé (pas de cache, queries
légères même pour 10k+ tickets). Retourne une map mémoire {user_id: set[segments]}
pour annotation rapide sans N+1.
"""
from datetime import datetime, timedelta
from typing import Dict, Set

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.event import Event
from models.ticket import Ticket


# Statuts considérés comme "achat confirmé" pour les segments
CONFIRMED_STATUSES = ("paye", "paye_cash")

# Statuts considérés comme "à valeur commerciale" pour l'export par défaut
COMMERCIAL_STATUSES = ("paye", "paye_cash", "reservation_cash", "en_attente_om")

# Statuts considérés comme abandonnés (toggle "inclure les abandons")
ABANDONED_STATUSES = ("expire_abandonne", "echec_paiement", "echec_init_ngser")

# Seuil "Gros payeurs" en FCFA (cumul 12 mois)
PREMIUM_THRESHOLD_FCFA = 50_000

# Fenêtres temporelles (jours)
PREMIUM_WINDOW_DAYS = 365
DORMANT_THRESHOLD_DAYS = 90


def compute_segments(db: Session, organizer_id: int) -> Dict[int, Set[str]]:
    """
    Calcule les 3 segments pour tous les acheteurs d'un organisateur.

    Retourne {user_id: {"recurrent", "premium", "dormant"}, ...}
    Map mémoire pour annotation rapide lors de l'export ou du listing.

    Performance : 3 requêtes agrégées indépendantes, ~50ms sur 10k tickets.
    """
    segments: Dict[int, Set[str]] = {}

    now = datetime.utcnow()
    floor_premium = now - timedelta(days=PREMIUM_WINDOW_DAYS)
    floor_dormant = now - timedelta(days=DORMANT_THRESHOLD_DAYS)

    base_join = (
        db.query(Ticket.user_id)
        .join(Event, Event.id == Ticket.event_id)
        .filter(Event.organizer_id == organizer_id)
        .filter(Ticket.payment_status.in_(CONFIRMED_STATUSES))
    )

    # 🔥 Récurrents : ≥2 events distincts payés
    recurrent_rows = (
        base_join
        .group_by(Ticket.user_id)
        .having(func.count(func.distinct(Ticket.event_id)) >= 2)
        .all()
    )
    for row in recurrent_rows:
        segments.setdefault(row.user_id, set()).add("recurrent")

    # 💰 Gros payeurs : ≥50 000 FCFA cumul sur 12 mois
    premium_rows = (
        db.query(Ticket.user_id)
        .join(Event, Event.id == Ticket.event_id)
        .filter(Event.organizer_id == organizer_id)
        .filter(Ticket.payment_status.in_(CONFIRMED_STATUSES))
        .filter(Ticket.purchased_at >= floor_premium)
        .group_by(Ticket.user_id)
        .having(func.coalesce(func.sum(Ticket.cash_amount), 0) >= PREMIUM_THRESHOLD_FCFA)
        .all()
    )
    for row in premium_rows:
        segments.setdefault(row.user_id, set()).add("premium")

    # 💤 Dormants : ≥1 achat lifetime, dernier achat >90 jours
    dormant_rows = (
        db.query(Ticket.user_id)
        .join(Event, Event.id == Ticket.event_id)
        .filter(Event.organizer_id == organizer_id)
        .filter(Ticket.payment_status.in_(CONFIRMED_STATUSES))
        .group_by(Ticket.user_id)
        .having(func.max(Ticket.purchased_at) < floor_dormant)
        .all()
    )
    for row in dormant_rows:
        segments.setdefault(row.user_id, set()).add("dormant")

    return segments


def get_segment_stats(db: Session, organizer_id: int) -> dict:
    """
    Stats globales des 3 segments pour l'orga.
    Retourne {recurrent: N, premium: M, dormant: K, total_unique_buyers: T}.
    """
    segments = compute_segments(db, organizer_id)

    recurrent_count = sum(1 for s in segments.values() if "recurrent" in s)
    premium_count = sum(1 for s in segments.values() if "premium" in s)
    dormant_count = sum(1 for s in segments.values() if "dormant" in s)

    return {
        "recurrent": recurrent_count,
        "premium": premium_count,
        "dormant": dormant_count,
        "total_unique_buyers": len(segments),
    }
