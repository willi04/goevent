"""models/promo.py — Codes promo (Chantier 19.A)"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, Numeric, DateTime,
    ForeignKey, CheckConstraint, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from core.database import Base


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id                = Column(Integer, primary_key=True, index=True)
    organizer_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id          = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=True)
    tier_name         = Column(String, nullable=True)
    code              = Column(String(20), nullable=False)
    discount_type     = Column(String, nullable=False)   # "percent" | "fixed"
    discount_value    = Column(Numeric, nullable=False)
    max_uses_global   = Column(Integer, nullable=True)
    max_uses_per_user = Column(Integer, nullable=False, default=1)
    used_count        = Column(Integer, nullable=False, default=0)
    starts_at         = Column(DateTime, nullable=True)
    ends_at           = Column(DateTime, nullable=True)
    is_active         = Column(Boolean, nullable=False, default=True)
    created_at        = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("discount_type IN ('percent', 'fixed')", name="chk_discount_type"),
        CheckConstraint("discount_value > 0", name="chk_discount_positive"),
        CheckConstraint(
            "discount_type != 'percent' OR discount_value <= 100",
            name="chk_percent_max"
        ),
        Index("idx_promo_event", "event_id", "is_active"),
        Index("idx_promo_org", "organizer_id"),
    )

    organizer = relationship("User", foreign_keys=[organizer_id])
    event     = relationship("Event", foreign_keys=[event_id])
    usages    = relationship("PromoUsage", back_populates="promo", cascade="all, delete-orphan")


class PromoUsage(Base):
    __tablename__ = "promo_usages"

    id            = Column(Integer, primary_key=True, index=True)
    promo_id      = Column(Integer, ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=False)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    ticket_id     = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    discount_fcfa = Column(Numeric, nullable=False)
    used_at       = Column(DateTime, nullable=False, default=datetime.utcnow)
    status        = Column(String, nullable=False, default="reserved")  # "reserved" | "confirmed" | "released"

    __table_args__ = (
        UniqueConstraint("promo_id", "ticket_id", name="uq_usage_promo_ticket"),
        CheckConstraint(
            "status IN ('reserved', 'confirmed', 'released')",
            name="chk_usage_status"
        ),
        Index("idx_usage_user", "promo_id", "user_id", "status"),
        Index("idx_usage_ticket", "ticket_id"),
    )

    promo  = relationship("PromoCode", back_populates="usages")
    user   = relationship("User", foreign_keys=[user_id])
    ticket = relationship("Ticket", foreign_keys=[ticket_id])
