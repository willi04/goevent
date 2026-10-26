"""schemas/invitation.py — Invitations digitales (événements privés)."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PrivateEventIn(BaseModel):
    title: str = Field(..., min_length=3, max_length=150)
    event_date: datetime
    location: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    client_name: str = Field(..., min_length=2, max_length=120)
    client_phone: Optional[str] = Field(None, max_length=30)
    host_label: Optional[str] = Field(None, max_length=120)
    service_level: str = Field("accompagne", max_length=20)
    message_template: Optional[str] = Field(None, max_length=1000)


class PrivateEventPatch(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=150)
    event_date: Optional[datetime] = None
    location: Optional[str] = Field(None, min_length=2, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    client_name: Optional[str] = Field(None, min_length=2, max_length=120)
    client_phone: Optional[str] = Field(None, max_length=30)
    host_label: Optional[str] = Field(None, max_length=120)
    service_level: Optional[str] = Field(None, max_length=20)
    message_template: Optional[str] = Field(None, max_length=1000)
    reset_message: bool = False


class GuestRow(BaseModel):
    line: Optional[int] = None
    full_name: Optional[str] = Field(None, max_length=300)
    phone: Optional[str] = Field(None, max_length=40)
    seats: Optional[str] = Field(None, max_length=10)
    table_label: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=100)


class GuestImportIn(BaseModel):
    rows: List[GuestRow] = Field(..., max_length=2000)
    dry_run: bool = True


class GuestPatch(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    phone: Optional[str] = Field(None, max_length=40)
    seats: Optional[int] = Field(None, ge=1, le=20)
    table_label: Optional[str] = Field(None, max_length=40)
    category: Optional[str] = Field(None, max_length=40)


class ManualCheckinIn(BaseModel):
    count: int = Field(..., ge=1, le=20)
    reason: str = Field(..., min_length=3, max_length=200)


class AgentIn(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    phone_number: str = Field(..., max_length=30)
    pin_code: str = Field(..., min_length=6, max_length=6)


class PackIn(BaseModel):
    size: int
    service: str = Field(..., max_length=20)
    payment_channel: str = Field(..., max_length=40)
    payment_ref: str = Field(..., max_length=80)
    amount_received: int = Field(..., ge=1, le=10_000_000)
    confirmed_goevent_account: bool = False
    note: Optional[str] = Field(None, max_length=300)


class PackCancelIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


class SyncItem(BaseModel):
    uuid: str = Field(..., max_length=40)
    h: str = Field(..., max_length=64)
    count: int = Field(1, ge=1, le=20)
    at: Optional[str] = Field(None, max_length=40)


class SyncIn(BaseModel):
    device_id: Optional[str] = Field(None, max_length=64)
    items: List[SyncItem] = Field(..., max_length=500)
