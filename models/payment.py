"""models/payment.py — Payment, PayoutRequest"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from core.database import Base


class Payment(Base):
    __tablename__ = "payments"
    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer)
    ticket_id        = Column(Integer)
    amount           = Column(Float)
    base_price       = Column(Float, default=0)
    platform_fee     = Column(Float, default=0)
    organizer_amount = Column(Float, default=0)
    status           = Column(String, default="pending")
    transaction_id   = Column(String)


class PayoutRequest(Base):
    __tablename__ = "payout_requests"
    id           = Column(Integer, primary_key=True, index=True)
    event_id     = Column(Integer, ForeignKey("events.id"))
    organizer_id = Column(Integer, ForeignKey("users.id"))
    amount       = Column(Float)
    status       = Column(String, default="en_attente")
    created_at   = Column(DateTime, default=datetime.utcnow)
