"""schemas/ads.py — Schémas Pydantic pour la régie publicitaire."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ════════════════════════════════════════════════════════════
# AdContract — Contrat commercial (interne admin)
# ════════════════════════════════════════════════════════════

class AdContractCreate(BaseModel):
    sponsor_name: str = Field(..., min_length=2, max_length=200)
    sponsor_contact: Optional[str] = None
    sponsor_email: Optional[str] = None
    sponsor_phone: Optional[str] = None
    amount_fcfa: float = Field(0, ge=0)
    start_date: datetime
    end_date: datetime
    status: str = Field("pending", pattern="^(pending|active|expired|cancelled)$")
    payment_status: str = Field("unpaid", pattern="^(unpaid|partial|paid)$")
    paid_amount: float = Field(0, ge=0)
    notes: Optional[str] = None

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("start_date")
        if start and v <= start:
            raise ValueError("end_date doit être postérieure à start_date")
        return v


class AdContractUpdate(BaseModel):
    sponsor_name: Optional[str] = Field(None, min_length=2, max_length=200)
    sponsor_contact: Optional[str] = None
    sponsor_email: Optional[str] = None
    sponsor_phone: Optional[str] = None
    amount_fcfa: Optional[float] = Field(None, ge=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[str] = Field(None, pattern="^(pending|active|expired|cancelled)$")
    payment_status: Optional[str] = Field(None, pattern="^(unpaid|partial|paid)$")
    paid_amount: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None


class AdContractOut(BaseModel):
    id: int
    sponsor_name: str
    sponsor_contact: Optional[str]
    sponsor_email: Optional[str]
    sponsor_phone: Optional[str]
    amount_fcfa: float
    start_date: datetime
    end_date: datetime
    status: str
    payment_status: str
    paid_amount: float
    notes: Optional[str]
    created_at: datetime
    created_by: Optional[int]

    class Config:
        from_attributes = True


# ════════════════════════════════════════════════════════════
# HomepageSlide — Créa affichée sur l'accueil
# ════════════════════════════════════════════════════════════

class HomepageSlideCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    subtitle: Optional[str] = Field(None, max_length=300)
    image_url: str = Field(..., min_length=10)  # base64 ou URL externe
    cta_text: Optional[str] = Field(None, max_length=50)
    cta_link: Optional[str] = Field(None, max_length=500)
    position: int = Field(0, ge=0, le=10)
    is_active: bool = True
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    ad_contract_id: Optional[int] = None

    @field_validator("ends_at")
    @classmethod
    def ends_after_starts(cls, v, info):
        starts = info.data.get("starts_at")
        if v and starts and v <= starts:
            raise ValueError("ends_at doit être postérieure à starts_at")
        return v


class HomepageSlideUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=200)
    subtitle: Optional[str] = Field(None, max_length=300)
    image_url: Optional[str] = Field(None, min_length=10)
    cta_text: Optional[str] = Field(None, max_length=50)
    cta_link: Optional[str] = Field(None, max_length=500)
    position: Optional[int] = Field(None, ge=0, le=10)
    is_active: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class HomepageSlideOut(BaseModel):
    """Vue admin — toutes les données métier."""
    id: int
    type: Optional[str]
    title: str
    subtitle: Optional[str]
    image_url: Optional[str]
    cta_text: Optional[str]
    cta_link: Optional[str]
    position: int
    is_active: bool
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    ad_contract_id: Optional[int]
    created_at: datetime
    sponsor_name: Optional[str] = None  # rempli côté route via jointure

    class Config:
        from_attributes = True


class HomepageSlidePublic(BaseModel):
    """Vue publique — zéro donnée commerciale exposée."""
    id: int
    title: str
    subtitle: Optional[str]
    image_url: Optional[str]
    cta_text: Optional[str]
    cta_link: Optional[str]

    class Config:
        from_attributes = True
