"""schemas/staff.py — Pydantic pour la gestion des employés staff GoEvent."""
import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator

from core.permissions import STAFF_FUNCTIONS_VALIDES


class StaffCreate(BaseModel):
    """Création d'un employé staff par l'admin."""
    full_name: str = Field(..., min_length=2, max_length=100)
    phone_number: str
    email: Optional[EmailStr] = None
    pin_code: str
    staff_function: str
    commission_rate: Optional[float] = Field(None, ge=0, le=1)  # 0.0 à 1.0 (0% à 100%)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v):
        if not re.match(r"^\+236\d{8}$", v):
            raise ValueError("Le numéro doit être au format RCA (+236XXXXXXXX)")
        return v

    @field_validator("pin_code")
    @classmethod
    def validate_pin(cls, v):
        if not re.match(r"^\d{4}$", v):
            raise ValueError("Le code PIN doit contenir exactement 4 chiffres")
        return v

    @field_validator("staff_function")
    @classmethod
    def validate_function(cls, v):
        if v not in STAFF_FUNCTIONS_VALIDES:
            raise ValueError(f"Fonction staff invalide. Valeurs : {STAFF_FUNCTIONS_VALIDES}")
        return v


class StaffUpdate(BaseModel):
    """Modification d'un staff par l'admin. Tous les champs optionnels."""
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    staff_function: Optional[str] = None
    commission_rate: Optional[float] = Field(None, ge=0, le=1)
    is_active: Optional[bool] = None

    @field_validator("staff_function")
    @classmethod
    def validate_function(cls, v):
        if v is not None and v not in STAFF_FUNCTIONS_VALIDES:
            raise ValueError(f"Fonction staff invalide. Valeurs : {STAFF_FUNCTIONS_VALIDES}")
        return v


class StaffOut(BaseModel):
    """Vue staff exposée à l'admin."""
    id: int
    full_name: str
    phone_number: str
    email: Optional[str]
    staff_function: Optional[str]
    commission_rate: Optional[float]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PermissionsOut(BaseModel):
    """Permissions du user connecté, exposées au frontend pour adapter l'UI."""
    user_id: int
    role: str
    role_normalized: str
    staff_function: Optional[str]
    is_admin: bool
    is_staff: bool
    capabilities: dict  # ex: {"ads_contracts": True, "ads_slides": False, ...}
