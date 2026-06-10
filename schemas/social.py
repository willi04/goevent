from typing import Optional
from pydantic import BaseModel


class FeedbackCreate(BaseModel):
    rating: Optional[int] = None
    category: Optional[str] = None
    message: str
    email_contact: Optional[str] = None
    page_origin: Optional[str] = None


class FeedbackUpdate(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None
