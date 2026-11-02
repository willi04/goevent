import re
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, validator


def _clean(v, n):
    return " ".join("".join(c for c in str(v or "") if ord(c) >= 32).split())[:n]


class PartnerRequestIn(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=20)
    partnership_type: Optional[str] = Field(None, max_length=60)
    message: str = Field(..., min_length=5, max_length=2000)

    @validator("company_name", "partnership_type", "message")
    def strip_controls(cls, v):
        return _clean(v, 2000) if v is not None else v

    @validator("phone")
    def check_phone(cls, v):
        v = (v or "").strip()
        if v and not re.match(r"^\+?[0-9 ]{6,20}$", v):
            raise ValueError("Numéro de téléphone invalide")
        return v or None


class FeedbackCreate(BaseModel):
    rating: Optional[int] = None
    category: Optional[str] = None
    message: str
    email_contact: Optional[str] = None
    page_origin: Optional[str] = None


class FeedbackUpdate(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None
