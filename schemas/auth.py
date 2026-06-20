import re
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, validator


class UserLogin(BaseModel):
    phone_number: str
    pin_code: str
    totp_code: Optional[str] = None


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserRegister(BaseModel):
    phone_number: str
    full_name: str = Field(..., min_length=2, max_length=100)
    pin_code: str
    role: str = "fan"
    email: Optional[EmailStr] = None
    org_name: Optional[str] = Field(None, max_length=200)
    org_type: Optional[str] = Field(None, max_length=100)

    @validator("phone_number")
    def validate_phone(cls, v):
        if not re.match(r"^\+236\d{8}$", v):
            raise ValueError("Le numéro doit être au format RCA (+236XXXXXXXX)")
        return v

    @validator("pin_code")
    def validate_pin(cls, v):
        if not re.match(r"^\d{4}$", v):
            raise ValueError("Le code PIN doit contenir exactement 4 chiffres")
        return v

    @validator("full_name")
    def validate_name(cls, v):
        if not re.match(r"^[a-zA-Z\s\-'.]+$", v):
            raise ValueError("Le nom contient des caractères non valides")
        return v


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    org_name: Optional[str] = Field(None, max_length=200)
    org_type: Optional[str] = Field(None, max_length=100)
    profile_picture: Optional[str] = Field(None, max_length=5242880)
    orange_money: Optional[str] = None

    @validator("orange_money")
    def validate_orange_money(cls, v):
        if v is None or v == "":
            return v
        v = v.strip()
        if not re.match(r"^\+236\d{8}$", v):
            raise ValueError("Le numéro Orange Money doit être au format RCA (+236XXXXXXXX)")
        return v


class PinChange(BaseModel):
    old_pin: str
    new_pin: str


class AgentCreate(BaseModel):
    full_name: str
    phone_number: str
    pin_code: str
    event_id: int
