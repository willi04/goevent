"""schemas/promo.py — Pydantic v2 (Chantier 19.A)"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ─── INPUT ─────────────────────────────────────────────────

class PromoCodeCreate(BaseModel):
    code: str = Field(..., min_length=3, max_length=20)
    discount_type: str = Field(..., pattern="^(percent|fixed)$")
    discount_value: float = Field(..., gt=0)
    event_id: Optional[int] = None
    tier_name: Optional[str] = None
    max_uses_global: Optional[int] = Field(None, gt=0)
    max_uses_per_user: int = Field(1, ge=1, le=100)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def code_format(cls, v: str) -> str:
        v = v.strip().upper()
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Le code ne doit contenir que des lettres, chiffres, '-' ou '_'")
        return v

    @field_validator("discount_value")
    @classmethod
    def percent_cap(cls, v, info):
        if info.data.get("discount_type") == "percent" and v > 100:
            raise ValueError("Une réduction en pourcentage ne peut excéder 100%")
        return v


class PromoCodeUpdate(BaseModel):
    """
    Modification limitée si used_count > 0 (audit). Détecté côté route.
    Champs autorisés en modification libre : is_active, ends_at, max_uses_global, max_uses_per_user.
    """
    is_active: Optional[bool] = None
    ends_at: Optional[datetime] = None
    max_uses_global: Optional[int] = Field(None, gt=0)
    max_uses_per_user: Optional[int] = Field(None, ge=1, le=100)
    discount_value: Optional[float] = Field(None, gt=0)
    starts_at: Optional[datetime] = None


class PromoValidateIn(BaseModel):
    """Validation publique : preview UX avant achat. Aucun side-effect."""
    event_id: int
    tier_name: Optional[str] = None
    code: str = Field(..., min_length=1, max_length=20)


# ─── OUTPUT ────────────────────────────────────────────────

class PromoCodeOut(BaseModel):
    id: int
    code: str
    discount_type: str
    discount_value: float
    event_id: Optional[int]
    event_title: Optional[str] = None
    tier_name: Optional[str]
    max_uses_global: Optional[int]
    max_uses_per_user: int
    used_count: int
    confirmed_count: int = 0
    total_discount_fcfa: float = 0
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    share_link: Optional[str] = None  # construit côté route, dépend du slug event

    class Config:
        from_attributes = True


class PromoValidateOut(BaseModel):
    valid: bool
    code: str
    message: str
    discount_fcfa: int = 0
    original_price: int = 0
    new_price: int = 0


class GeneratedCodeOut(BaseModel):
    code: str
