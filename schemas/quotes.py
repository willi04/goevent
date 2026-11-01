"""schemas/quotes.py — Devis numérotés de l'offre Invitations (lot 4.5)."""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from schemas.invitation_catalog import QuoteIn


class QuoteClientIn(BaseModel):
    client_name: str = Field(..., min_length=2, max_length=120)
    client_company: Optional[str] = Field(None, max_length=120)
    client_phone: Optional[str] = Field(None, max_length=30)
    event_type: Optional[str] = Field(None, max_length=60)
    event_date: Optional[date] = None
    location: Optional[str] = Field(None, max_length=200)
    guests_count: Optional[int] = Field(None, ge=1, le=100000)


class QuoteCreateIn(QuoteClientIn):
    order: QuoteIn                                  # formule, pack / sur mesure, options, remise
    event_id: Optional[int] = None                  # « Préparer un devis » depuis un événement
    owner_id: Optional[int] = None                  # admin : commercial propriétaire (vide = lui-même)
    force_reason: Optional[str] = Field(None, max_length=300)   # admin : passer outre un conflit


class QuotePatchIn(QuoteClientIn):
    order: QuoteIn
    force_reason: Optional[str] = Field(None, max_length=300)


class QuoteRefuseIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


class QuoteOwnerIn(BaseModel):
    owner_id: int


class QuoteConvertIn(BaseModel):
    title: str = Field(..., min_length=3, max_length=150)
    event_date: str = Field(..., max_length=40)     # date + heure (ISO) ; la date du devis sert de défaut
    location: str = Field(..., min_length=2, max_length=200)
    host_label: Optional[str] = Field(None, max_length=120)


class CommissionPayIn(BaseModel):
    paid_on: date
    reference: str = Field(..., min_length=3, max_length=80)
