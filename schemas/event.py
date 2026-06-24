from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class EventCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field("", max_length=5000)
    location: str = Field(..., min_length=2, max_length=100)
    category: str = Field("Concert", max_length=50)
    event_date: datetime
    price: float = Field(..., ge=0, le=1000000)
    total_seats: int = Field(..., ge=1, le=100000)
    cover_image_url: Optional[str] = None  # base64 ou URL — pas de max_length
    ticket_tiers: Optional[list] = []

    @field_validator("event_date")
    @classmethod
    def validate_date(cls, v):
        now = datetime.now(timezone.utc)
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        if v < now:
            raise ValueError("L'événement ne peut pas être dans le passé")
        if v > now + timedelta(days=730):
            raise ValueError("La date est trop lointaine (max 2 ans)")
        return v

    @field_validator("cover_image_url")
    @classmethod
    def validate_image_url(cls, v):
        if v is None:
            return v
        if not v.startswith(("https://", "http://", "data:image/")):
            raise ValueError("Format image invalide")
        return v

    @field_validator("description")
    @classmethod
    def sanitize_description(cls, v):
        from bleach import clean
        return clean(
            v,
            tags=["p", "br", "strong", "em", "u", "a"],
            attributes={"a": ["href"]},
            strip=True,
        )


class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    category: Optional[str] = None
    event_date: Optional[datetime] = None
    price: Optional[float] = None
    total_seats: Optional[int] = None
    cover_image_url: Optional[str] = None
    is_active: Optional[bool] = None
