"""models/event.py — Event"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from core.database import Base


class Event(Base):
    __tablename__ = "events"
    id              = Column(Integer, primary_key=True, index=True)
    title           = Column(String)
    description     = Column(Text, default="")
    location        = Column(String)
    category        = Column(String, default="Concert")
    event_date      = Column(DateTime)
    price           = Column(Float)
    total_seats     = Column(Integer)
    seats_sold      = Column(Integer, default=0)
    cover_image_url = Column(String, default="")
    ticket_tiers    = Column(JSONB, default=list)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    organizer_id    = Column(Integer, ForeignKey("users.id"))

    organizer_user = relationship("User", back_populates="events", foreign_keys=[organizer_id])
    tickets        = relationship("Ticket", back_populates="event")
