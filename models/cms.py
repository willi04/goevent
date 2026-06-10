"""models/cms.py — HomepageContent, AdContract, FundraisingCampaign"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from core.database import Base


class HomepageContent(Base):
    __tablename__ = "homepage_content"
    id               = Column(Integer, primary_key=True, index=True)
    type             = Column(String, index=True)
    title            = Column(String, nullable=False)
    subtitle         = Column(String, nullable=True)
    description      = Column(Text, nullable=True)
    image_url        = Column(String, nullable=True)
    cta_text         = Column(String, nullable=True)
    cta_link         = Column(String, nullable=True)
    background_color = Column(String, nullable=True)
    text_color       = Column(String, nullable=True)
    position         = Column(Integer, default=0)
    is_active        = Column(Boolean, default=True)
    starts_at        = Column(DateTime, nullable=True)
    ends_at          = Column(DateTime, nullable=True)
    impressions      = Column(Integer, default=0)
    clicks           = Column(Integer, default=0)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by       = Column(Integer, ForeignKey("users.id"))
    ad_contract_id   = Column(Integer, ForeignKey("ad_contracts.id"), nullable=True)

    creator = relationship("User", foreign_keys=[created_by])


class AdContract(Base):
    __tablename__ = "ad_contracts"
    id              = Column(Integer, primary_key=True, index=True)
    sponsor_name    = Column(String, nullable=False)
    sponsor_contact = Column(String, nullable=True)
    sponsor_email   = Column(String, nullable=True)
    sponsor_phone   = Column(String, nullable=True)
    amount_fcfa     = Column(Float, default=0)
    start_date      = Column(DateTime, nullable=False)
    end_date        = Column(DateTime, nullable=False)
    status          = Column(String, default="pending")
    payment_status  = Column(String, default="unpaid")
    paid_amount     = Column(Float, default=0)
    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    created_by      = Column(Integer, ForeignKey("users.id"))

    creator = relationship("User", foreign_keys=[created_by])


class FundraisingCampaign(Base):
    __tablename__ = "fundraising_campaigns"
    id                = Column(Integer, primary_key=True, index=True)
    title             = Column(String, nullable=False)
    description       = Column(Text, nullable=False)
    image_url         = Column(String, nullable=True)
    beneficiary_name  = Column(String, nullable=False)
    beneficiary_phone = Column(String, nullable=True)
    beneficiary_email = Column(String, nullable=True)
    goal_fcfa         = Column(Float, nullable=False)
    collected_fcfa    = Column(Float, default=0)
    starts_at         = Column(DateTime, default=datetime.utcnow)
    ends_at           = Column(DateTime, nullable=True)
    is_active         = Column(Boolean, default=True)
    is_verified       = Column(Boolean, default=False)
    created_at        = Column(DateTime, default=datetime.utcnow)
    created_by        = Column(Integer, ForeignKey("users.id"))

    creator = relationship("User", foreign_keys=[created_by])
