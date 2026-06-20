"""
Chantier 17 : Pré-inscription Services Premium (QR perso, tickets cadeaux, etc.)
Marketing capture — pas de paiement, juste un email pour notifier au lancement.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from core.database import Base


class PremiumInterest(Base):
    __tablename__ = "premium_interests"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(200), nullable=False, index=True)
    services = Column(Text, nullable=True)  # CSV des services cochés
    source = Column(String(50), nullable=True, default="partenaire_page")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
