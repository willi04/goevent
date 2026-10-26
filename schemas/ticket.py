from typing import Optional
from pydantic import BaseModel, Field


class TicketTier(BaseModel):
    name: str
    price: float
    seats: int


class BuyTicket(BaseModel):
    event_id: int


class ScanTicket(BaseModel):
    qr_data: str = Field(..., max_length=500)
    count: Optional[int] = Field(None, ge=1, le=20)   # invitations : personnes qui entrent avec ce pass
    # invitations : identifiant unique de la tentative (généré par le scanner). Si la
    # réponse se perd (réseau lent) et que le scanner bascule hors ligne, la synchro
    # réutilise le même identifiant → aucune double entrée comptée.
    client_uuid: Optional[str] = Field(None, max_length=40)


class CashReservation(BaseModel):
    event_id: int
    tier_name: Optional[str] = None
    email: Optional[str] = None
    promo_code: Optional[str] = None
