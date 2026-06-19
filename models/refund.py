"""models/refund.py — RefundRequest"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from core.database import Base


class RefundRequest(Base):
    __tablename__ = "refund_requests"

    id            = Column(Integer, primary_key=True, index=True)
    ticket_id     = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id      = Column(Integer, ForeignKey("events.id"), nullable=True)
    event_title   = Column(String(500), nullable=False)
    amount        = Column(Float, nullable=False)
    phone_number  = Column(String(20), nullable=False)
    status        = Column(String(30), default="a_rembourser", nullable=False)
    payment_proof = Column(String(200), nullable=True)
    admin_note    = Column(Text, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    paid_at       = Column(DateTime, nullable=True)
    paid_by       = Column(Integer, ForeignKey("users.id"), nullable=True)

    ticket = relationship("Ticket", foreign_keys=[ticket_id])
    user   = relationship("User", foreign_keys=[user_id])
    event  = relationship("Event", foreign_keys=[event_id])
    payer  = relationship("User", foreign_keys=[paid_by])
