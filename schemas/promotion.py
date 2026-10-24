"""
Chantier 17 : Schémas Pydantic pour Boost orga + Pack FB Ads.
"""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator


# ─── Boost organisateur ──────────────────────────────────────────────

class BoostInitiateIn(BaseModel):
    """Payload : l'orga lance un boost forfaitaire."""
    event_id: int
    package_code: Literal["7d", "30d", "90d"]
    phone: str = Field(..., min_length=8, max_length=20)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if v.startswith("00236"):
            v = "+" + v[2:]
        elif v.startswith("236") and not v.startswith("+236"):
            v = "+" + v
        elif not v.startswith("+"):
            v = "+236" + v
        if not v.startswith("+236") or len(v) != 12 or not v[1:].isdigit():
            raise ValueError("Numéro Orange Money invalide (format attendu +236XXXXXXXX)")
        return v


class BoostInitiateOut(BaseModel):
    payment_url: str
    payment_ref: str
    amount_fcfa: int
    boost_order_id: int


class BoostOrderOut(BaseModel):
    id: int
    event_id: int
    event_title: str
    package_code: str
    duration_days: int
    amount_fcfa: int
    status: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Pack Facebook Ads ───────────────────────────────────────────────

class FbPackRequestIn(BaseModel):
    """Payload : l'orga demande un Pack FB."""
    event_id: int
    budget_fb_fcfa: int = Field(..., ge=1, le=10_000_000)  # minimum en vigueur vérifié dans la route (tarifs back-office)
    phone: str = Field(..., min_length=8, max_length=20)
    age_range: Optional[str] = Field(None, max_length=20)
    zone: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=1000)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if v.startswith("00236"):
            v = "+" + v[2:]
        elif v.startswith("236") and not v.startswith("+236"):
            v = "+" + v
        elif not v.startswith("+"):
            v = "+236" + v
        if not v.startswith("+236") or len(v) != 12 or not v[1:].isdigit():
            raise ValueError("Numéro Orange Money invalide")
        return v


class FbPackRequestOut(BaseModel):
    payment_url: str
    payment_ref: str
    budget_fb_fcfa: int
    margin_fcfa: int
    total_paid_fcfa: int
    fb_package_id: int


class FbPackOut(BaseModel):
    id: int
    event_id: int
    event_title: str
    budget_fb_fcfa: int
    margin_fcfa: int
    total_paid_fcfa: int
    age_range: Optional[str] = None
    zone: Optional[str] = None
    notes: Optional[str] = None
    utm_campaign: str
    status: str
    fb_campaign_id: Optional[str] = None
    cm_notes: Optional[str] = None
    created_at: datetime
    launched_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ─── Admin CM : mise à jour Pack FB ──────────────────────────────────

class FbPackAdminUpdateIn(BaseModel):
    """Le CM met à jour un Pack FB (action depuis admin.html)."""
    action: Literal["launch", "complete", "cancel"]
    fb_campaign_id: Optional[str] = Field(None, max_length=100)
    cm_notes: Optional[str] = Field(None, max_length=1000)
