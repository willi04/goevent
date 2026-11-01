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
    # "public" (billetterie) | "private" (invitations digitales : jamais listé,
    # jamais vendu — cf. models/invitation.py). Migration : core/migrations.py
    visibility      = Column(String(10), nullable=False, default="public", server_default="public")
    created_at      = Column(DateTime, default=datetime.utcnow)
    # Désactivation par l'équipe (lot 5) : motif obligatoire, journalisé (security_logs).
    # Colonnes lues par toutes les routes events → ALTER à la main sur Neon AVANT le push.
    deactivated_reason = Column(String(300), nullable=True)
    deactivated_at     = Column(DateTime, nullable=True)
    deactivated_by     = Column(Integer, nullable=True)
    organizer_id    = Column(Integer, ForeignKey("users.id"))

    organizer_user = relationship("User", back_populates="events", foreign_keys=[organizer_id])
    tickets        = relationship("Ticket", back_populates="event")
