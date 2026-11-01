from typing import Optional
from pydantic import BaseModel, Field

from core.orders import MAX_PER_ORDER


class PaymentConfirm(BaseModel):
    event_id: int
    email: Optional[str] = None
    full_name: Optional[str] = None
    tier_name: Optional[str] = None
    promo_code: Optional[str] = None
    quantity: int = Field(1, ge=1, le=MAX_PER_ORDER)   # lot 5.5 : N billets, un seul paiement

class OrderCreateLegacy(BaseModel):
    event_id: int
    buyer_name: str
    buyer_phone: str
    tier_name: str
    price: float
