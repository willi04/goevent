from typing import Optional
from pydantic import BaseModel


class TicketTier(BaseModel):
    name: str
    price: float
    seats: int


class BuyTicket(BaseModel):
    event_id: int


class ScanTicket(BaseModel):
    qr_data: str


class CashReservation(BaseModel):
    event_id: int
    tier_name: Optional[str] = None
    email: Optional[str] = None
