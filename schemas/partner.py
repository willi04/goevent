from pydantic import BaseModel


class PartnerRequestCreate(BaseModel):
    company_name: str
    email: str
    phone: str
    partnership_type: str
    message: str


class CancelRequestCreate(BaseModel):
    reason: str
