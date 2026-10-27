"""schemas/invitation_catalog.py — Catalogue des packs Invitations (lot 2.2)."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

MAX_PRICE = 50_000_000


class FormulaPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=60)
    description: Optional[str] = Field(None, max_length=300)
    included: Optional[List[str]] = Field(None, max_length=12)
    badge: Optional[str] = Field(None, max_length=30)
    public: Optional[bool] = None
    onsite_agents: Optional[bool] = None
    track_link: Optional[bool] = None
    team_sends: Optional[bool] = None
    agents_step: Optional[int] = Field(None, ge=1, le=5000)
    sort_order: Optional[int] = Field(None, ge=0, le=10_000)


class FormulaIn(FormulaPatch):
    code: str = Field(..., pattern=r"^[a-z][a-z0-9_]{1,19}$")   # immuable
    name: str = Field(..., min_length=2, max_length=60)


class CatalogPackPatch(BaseModel):
    size: Optional[int] = Field(None, ge=1, le=5000)
    price_fcfa: Optional[int] = Field(None, ge=0, le=MAX_PRICE)
    on_quote: Optional[bool] = None
    name: Optional[str] = Field(None, max_length=60)
    public: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0, le=10_000)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None


class CatalogPackIn(CatalogPackPatch):
    formula_id: int
    size: int = Field(..., ge=1, le=5000)


class OptionPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=80)
    description: Optional[str] = Field(None, max_length=300)
    price_fcfa: Optional[int] = Field(None, ge=0, le=MAX_PRICE)
    unit: Optional[str] = Field(None, pattern=r"^(flat|per_unit|per_page)$")
    unit_label: Optional[str] = Field(None, max_length=30)
    max_qty: Optional[int] = Field(None, ge=1, le=1000)
    agents_per_unit: Optional[int] = Field(None, ge=0, le=20)
    public: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0, le=10_000)
    formula_ids: Optional[List[int]] = Field(None, max_length=50)


class OptionIn(OptionPatch):
    name: str = Field(..., min_length=2, max_length=80)
    price_fcfa: int = Field(..., ge=0, le=MAX_PRICE)
    unit: str = Field("flat", pattern=r"^(flat|per_unit|per_page)$")
    formula_ids: List[int] = Field(..., min_length=1, max_length=50)


class DiscountPatch(BaseModel):
    kind: Optional[str] = Field(None, pattern=r"^(pct|fixed)$")
    value: Optional[int] = Field(None, ge=1, le=MAX_PRICE)
    max_uses: Optional[int] = Field(None, ge=1, le=100_000)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    formula_ids: Optional[List[int]] = Field(None, max_length=50)
    pack_ids: Optional[List[int]] = Field(None, max_length=200)
    note: Optional[str] = Field(None, max_length=200)


class DiscountIn(DiscountPatch):
    code: str = Field(..., min_length=3, max_length=40)
    kind: str = Field(..., pattern=r"^(pct|fixed)$")
    value: int = Field(..., ge=1, le=MAX_PRICE)


class CatalogSettingsIn(BaseModel):
    manual_discount_max_pct: Optional[int] = Field(None, ge=0, le=100)
    vat_enabled: Optional[bool] = None
    vat_rate: Optional[float] = Field(None, ge=0, le=50)          # en % (ex. 19 ou 19,25)
    prices_include_vat: Optional[bool] = None


class CustomPackIn(BaseModel):
    size: int = Field(..., ge=1, le=5000)
    price_fcfa: int = Field(..., ge=0, le=MAX_PRICE)
    reason: str = Field(..., max_length=300)


class OptionLineIn(BaseModel):
    option_id: int
    qty: int = Field(1, ge=0, le=1000)


class ManualDiscountIn(BaseModel):
    kind: str = Field(..., pattern=r"^(pct|fixed)$")
    value: int = Field(..., ge=1, le=MAX_PRICE)
    reason: str = Field(..., max_length=300)


class QuoteIn(BaseModel):
    formula_id: int
    pack_id: Optional[int] = None
    custom: Optional[CustomPackIn] = None
    options: List[OptionLineIn] = Field(default_factory=list, max_length=30)
    discount_code: Optional[str] = Field(None, max_length=40)
    manual_discount: Optional[ManualDiscountIn] = None
    offered: bool = False
    offered_reason: Optional[str] = Field(None, max_length=300)


class OrderIn(QuoteIn):
    payment_channel: Optional[str] = Field(None, max_length=40)
    payment_ref: Optional[str] = Field(None, max_length=80)
    amount_received: Optional[int] = Field(None, ge=0, le=MAX_PRICE * 2)
    confirmed_goevent_account: bool = False
    note: Optional[str] = Field(None, max_length=300)
