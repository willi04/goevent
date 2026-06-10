from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class HomepageContentCreate(BaseModel):
    type: str
    title: str
    subtitle: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    cta_text: Optional[str] = None
    cta_link: Optional[str] = None
    background_color: Optional[str] = None
    text_color: Optional[str] = None
    position: int = 0
    is_active: bool = True
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    ad_contract_id: Optional[int] = None


class HomepageContentUpdate(BaseModel):
    type: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    cta_text: Optional[str] = None
    cta_link: Optional[str] = None
    background_color: Optional[str] = None
    text_color: Optional[str] = None
    position: Optional[int] = None
    is_active: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class AdContractCreate(BaseModel):
    sponsor_name: str
    sponsor_contact: Optional[str] = None
    sponsor_email: Optional[str] = None
    sponsor_phone: Optional[str] = None
    amount_fcfa: float = 0
    start_date: datetime
    end_date: datetime
    notes: Optional[str] = None


class FundraisingCampaignCreate(BaseModel):
    title: str
    description: str
    image_url: Optional[str] = None
    beneficiary_name: str
    beneficiary_phone: Optional[str] = None
    beneficiary_email: Optional[str] = None
    goal_fcfa: float
    ends_at: Optional[datetime] = None
