"""models/partner.py — PartnerRequest, CancellationRequest"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from core.database import Base


class PartnerRequest(Base):
    __tablename__ = "partner_requests"
    id               = Column(Integer, primary_key=True, index=True)
    company_name     = Column(String)
    email            = Column(String)
    phone            = Column(String)
    partnership_type = Column(String)
    message          = Column(Text)
    status           = Column(String, default="nouveau")
    created_at       = Column(DateTime, default=datetime.utcnow)


class CancellationRequest(Base):
    __tablename__ = "cancellation_requests"
    id           = Column(Integer, primary_key=True, index=True)
    event_id     = Column(Integer, ForeignKey("events.id"))
    organizer_id = Column(Integer, ForeignKey("users.id"))
    reason       = Column(Text)
    status       = Column(String, default="en_attente")
    created_at   = Column(DateTime, default=datetime.utcnow)

    event     = relationship("Event")
    organizer = relationship("User")
