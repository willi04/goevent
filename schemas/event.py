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

    @field_validator("ticket_tiers")
    @classmethod
    def validate_tiers(cls, v):
        # JSONB libre avant : tier.name rendu en innerHTML sur event_detail
        # (XSS orga → tous les fans) + price non numérique → 500 à l'achat.
        if not v:
            return []
        if len(v) > 20:
            raise ValueError("20 catégories de billets maximum")
        from bleach import clean
        out = []
        for t in v:
            if not isinstance(t, dict):
                raise ValueError("Catégorie de billet invalide")
            name = clean(str(t.get("name", "")), tags=[], strip=True).strip()[:60]
            if not name:
                raise ValueError("Nom de catégorie obligatoire")
            try:
                price = float(t.get("price", 0))
            except (TypeError, ValueError):
                raise ValueError(f"Prix invalide pour la catégorie {name}")
            if price < 0 or price > 1_000_000:
                raise ValueError(f"Prix hors limites pour la catégorie {name}")
            out.append({**t, "name": name, "price": price})
        return out


class EventUpdate(BaseModel):
    # Mêmes bornes que EventCreate : avant, PUT contournait toutes les
    # validations (prix négatif, places ≤ 0, description non nettoyée).
    # is_active RETIRÉ : un orga pouvait réactiver un event dépublié par l'admin.
    # Dépublication orga = DELETE /events/{id}.
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    location: Optional[str] = Field(None, min_length=2, max_length=100)
    category: Optional[str] = Field(None, max_length=50)
    event_date: Optional[datetime] = None
    price: Optional[float] = Field(None, ge=0, le=1000000)
    total_seats: Optional[int] = Field(None, ge=1, le=100000)
    cover_image_url: Optional[str] = None

    @field_validator("description")
    @classmethod
    def sanitize_description(cls, v):
        return EventCreate.sanitize_description(v) if v is not None else v

    @field_validator("cover_image_url")
    @classmethod
    def validate_image_url(cls, v):
        return EventCreate.validate_image_url(v)
