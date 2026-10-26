from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ContentValuesUpdate(BaseModel):
    # valeur null / "" / égale au défaut → la surcharge est supprimée (retour au texte du HTML)
    values: Dict[str, Optional[str]] = Field(..., max_length=200)


class PageRevisionCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    body_html: str = Field(..., min_length=1, max_length=300_000)
    note: Optional[str] = Field(None, max_length=300)


class FaqItemCreate(BaseModel):
    category: str = Field(..., max_length=20)
    question: str = Field(..., min_length=3, max_length=300)
    answer_html: str = Field(..., min_length=1, max_length=10_000)
    is_active: bool = True


class FaqItemUpdate(BaseModel):
    category: Optional[str] = Field(None, max_length=20)
    question: Optional[str] = Field(None, min_length=3, max_length=300)
    answer_html: Optional[str] = Field(None, min_length=1, max_length=10_000)
    is_active: Optional[bool] = None


class FaqOrder(BaseModel):
    ids: List[int] = Field(..., max_length=500)


class FaqImport(BaseModel):
    items: List[FaqItemCreate] = Field(..., min_length=1, max_length=200)
    replace: bool = False


class ZoneCard(BaseModel):
    icon: Optional[str] = Field(None, max_length=40)
    title: str = Field(..., min_length=1, max_length=60)
    text: Optional[str] = Field(None, max_length=240)


class ZoneBlockIn(BaseModel):
    kind: str = Field(..., max_length=20)
    title: Optional[str] = Field(None, max_length=160)
    body: Optional[str] = Field(None, max_length=1000)
    image: Optional[str] = Field(None, max_length=2_200_000)  # data URL ≤ 1,5 Mo binaire
    link_url: Optional[str] = Field(None, max_length=500)
    link_label: Optional[str] = Field(None, max_length=60)
    sponsor: Optional[str] = Field(None, max_length=120)
    theme: str = Field("green", max_length=20)
    items: Optional[List[ZoneCard]] = Field(None, max_length=8)
    is_active: bool = True
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class ZoneBlockPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=160)
    body: Optional[str] = Field(None, max_length=1000)
    image: Optional[str] = Field(None, max_length=2_200_000)
    link_url: Optional[str] = Field(None, max_length=500)
    link_label: Optional[str] = Field(None, max_length=60)
    sponsor: Optional[str] = Field(None, max_length=120)
    theme: Optional[str] = Field(None, max_length=20)
    items: Optional[List[ZoneCard]] = Field(None, max_length=8)
    is_active: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    clear_dates: bool = False   # true → retire les dates de diffusion


class ZoneSettingsIn(BaseModel):
    every: Optional[int] = Field(None, ge=3, le=24)


class ZoneOrder(BaseModel):
    ids: List[int] = Field(..., max_length=200)


class PricingIn(BaseModel):
    pricing: Dict[str, Dict[str, Optional[float]]]
