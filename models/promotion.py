"""
Chantier 17 : Régie publicitaire forfaitaire.
- BoostOrder : forfait boost organisateur (10k/35k/130k pour 7j/30j/90j)
- FbAdPackage : Pack Facebook Ads géré par le CM (min 35k + 30% marge)
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from core.database import Base


# ─── Boost organisateur (forfait) ─────────────────────────────────────

class BoostOrder(Base):
    """
    Boost forfaitaire : event remonte en tête de liste sur /events
    pendant la durée du package, avec badge "Mis en avant".
    """
    __tablename__ = "boost_orders"

    id = Column(Integer, primary_key=True, index=True)
    organizer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)

    # Future-proof : 'forfait' aujourd'hui, 'cpc'/'cpm' plus tard sans refacto
    boost_type = Column(String(20), nullable=False, default="forfait")

    # Code package : '7d' / '30d' / '90d'
    package_code = Column(String(10), nullable=False)
    duration_days = Column(Integer, nullable=False)
    amount_fcfa = Column(Integer, nullable=False)

    # Statuts : en_attente_om | actif | expire | annule | echec_paiement | expire_abandonne
    status = Column(String(30), nullable=False, default="en_attente_om", index=True)
    payment_ref = Column(String(100), nullable=True, index=True)

    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    # Relations
    organizer = relationship("User", foreign_keys=[organizer_id])
    event = relationship("Event", foreign_keys=[event_id])


# ─── Pack Facebook Ads ────────────────────────────────────────────────

class FbAdPackage(Base):
    """
    Pack Facebook Ads : l'orga paye un budget + 30% de marge GoEvent.
    Le CM lance et gère la campagne sur FB Ads Manager.
    Min budget Facebook : 35 000 FCFA (donc min total payé : 45 500 FCFA).
    """
    __tablename__ = "fb_ad_packages"

    id = Column(Integer, primary_key=True, index=True)
    organizer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)

    # Budget réellement injecté dans FB Ads par le CM (min 35 000)
    budget_fb_fcfa = Column(Integer, nullable=False)
    # Marge GoEvent (30% du budget FB)
    margin_fcfa = Column(Integer, nullable=False)
    # Ce que l'orga paye (budget_fb + margin)
    total_paid_fcfa = Column(Integer, nullable=False)

    # Brief ciblage (optionnel)
    age_range = Column(String(20), nullable=True)
    zone = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    # UTM unique pour tracker le ROI côté GoEvent
    utm_campaign = Column(String(100), nullable=False, unique=True, index=True)

    # Statuts : en_attente_om | paye_attente_cm | actif | termine | annule | echec_paiement | expire_abandonne
    status = Column(String(30), nullable=False, default="en_attente_om", index=True)
    payment_ref = Column(String(100), nullable=True, index=True)

    # Renseigné par le CM quand il lance la campagne FB
    fb_campaign_id = Column(String(100), nullable=True)
    cm_notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    launched_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    # Relations
    organizer = relationship("User", foreign_keys=[organizer_id])
    event = relationship("Event", foreign_keys=[event_id])
