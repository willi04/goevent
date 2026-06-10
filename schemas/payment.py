from typing import Optional
from pydantic import BaseModel


class PaymentConfirm(BaseModel):
    event_id: int
    email: Optional[str] = None
    full_name: Optional[str] = None
    tier_name: Optional[str] = None


class OrderCreateLegacy(BaseModel):
    event_id: int
    buyer_name: str
    buyer_phone: str
    tier_name: str
    price: float
