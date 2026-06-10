"""models/social.py — Favorite, Follower, Feedback"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from core.database import Base


class Favorite(Base):
    __tablename__ = "favorites"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"))
    event_id   = Column(Integer, ForeignKey("events.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    user  = relationship("User", back_populates="favorites")
    event = relationship("Event")


class Follower(Base):
    __tablename__ = "followers"
    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey("users.id"))
    organizer_id = Column(Integer, ForeignKey("users.id"))
    created_at   = Column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    __tablename__ = "feedbacks"
    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=True)
    rating        = Column(Integer, nullable=True)
    category      = Column(String, nullable=True)
    message       = Column(Text, nullable=False)
    email_contact = Column(String, nullable=True)
    user_agent    = Column(String, nullable=True)
    page_origin   = Column(String, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    status        = Column(String, default="nouveau")
    admin_note    = Column(Text, nullable=True)
    read_at       = Column(DateTime, nullable=True)

    user = relationship("User", foreign_keys=[user_id])
