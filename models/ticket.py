"""models/ticket.py — Ticket"""
import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from core.database import Base


class Ticket(Base):
    __tablename__ = "tickets"
    id             = Column(Integer, primary_key=True, index=True)
    qr_hash        = Column(String, unique=True, index=True, default=lambda: str(uuid.uuid4()))
    payment_status = Column(String, default="attente")
    payment_ref    = Column(String, default="")
    is_used        = Column(Boolean, default=False)
    used_at        = Column(DateTime, nullable=True)
    purchased_at   = Column(DateTime, default=datetime.utcnow)
    user_id        = Column(Integer, ForeignKey("users.id"))
    event_id       = Column(Integer, ForeignKey("events.id"))
    payment_method         = Column(String, nullable=True)
    reservation_expires_at = Column(DateTime, nullable=True)
    cash_collected_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    cash_collected_at      = Column(DateTime, nullable=True)
    cash_amount            = Column(Float, nullable=True)

    owner          = relationship("User", back_populates="tickets", foreign_keys=[user_id])
    event          = relationship("Event", back_populates="tickets")
    cash_collector = relationship("User", foreign_keys=[cash_collected_by])
    failure_reason = Column(String(50), nullable=True)
    # Renseigné quand payment_status passe à echec_paiement :
    # - "insufficient_balance" : NGSER code 2 (solde OM insuffisant)
    # - "cancelled" : NGSER cancellation=true (annulé par le fan)
    # - "rejected" : NGSER code 0/4/5/6 (autres échecs)
