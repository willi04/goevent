"""models/user.py — User, SecurityLog, PasswordReset"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from core.database import Base


class User(Base):
    __tablename__ = "users"
    id           = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    full_name    = Column(String)
    email        = Column(String, nullable=True)
    pin_hash     = Column(String)
    profile_picture = Column(Text, nullable=True)
    role         = Column(String, default="fan")
    org_name     = Column(String, nullable=True)
    org_type     = Column(String, nullable=True)
    is_active    = Column(Boolean, default=True)
    agent_event_id = Column(Integer, ForeignKey("events.id"), nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    failed_login_attempts = Column(Integer, default=0)
    locked_until          = Column(DateTime, nullable=True)
    two_factor_secret = Column(String, nullable=True)
    is_2fa_enabled = Column(Boolean, default=False)
    orange_money = Column(String(20), nullable=True)

    events    = relationship("Event", back_populates="organizer_user", foreign_keys="[Event.organizer_id]")
    tickets   = relationship("Ticket", back_populates="owner", foreign_keys="Ticket.user_id")
    favorites = relationship("Favorite", back_populates="user")


class SecurityLog(Base):
    __tablename__ = "security_logs"
    id          = Column(Integer, primary_key=True, index=True)
    event_type  = Column(String, index=True)
    user_id     = Column(Integer, nullable=True)
    ip_address  = Column(String, nullable=True)
    user_agent  = Column(String, nullable=True)
    details     = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow, index=True)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id         = Column(Integer, primary_key=True, index=True)
    email      = Column(String)
    code       = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
