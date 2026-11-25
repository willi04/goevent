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
    owner_id: Optional[int] = None          # admin : commercial propriétaire (vide = maison)


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
    paper: Optional[str] = Field(None, max_length=20)   # « oui » / « non » / vide (= papier si pas de numéro)


class GuestImportIn(BaseModel):
    rows: List[GuestRow] = Field(..., max_length=2000)
    dry_run: bool = True


class GuestPatch(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=120)
    phone: Optional[str] = Field(None, max_length=40)
    seats: Optional[int] = Field(None, ge=1, le=20)
    table_label: Optional[str] = Field(None, max_length=40)
    category: Optional[str] = Field(None, max_length=40)
    paper: Optional[bool] = None


class ManualCheckinIn(BaseModel):
    count: int = Field(..., ge=1, le=20)
    reason: str = Field(..., min_length=3, max_length=200)


class AgentManualIn(BaseModel):
    """Entrée manuelle depuis le scanner : recherche par nom, vérification, motif obligatoire."""
    guest_id: int
    count: int = Field(..., ge=1, le=20)
    reason: str = Field(..., min_length=3, max_length=180)
    last4: Optional[str] = Field(None, max_length=10)
    identity_checked: bool = False
    client_uuid: Optional[str] = Field(None, max_length=40)


class AgentIn(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    phone_number: str = Field(..., max_length=30)
    pin_code: str = Field(..., min_length=6, max_length=6)


class PackCancelIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


class SyncItem(BaseModel):
    uuid: str = Field(..., max_length=40)
    h: str = Field(..., max_length=64)
    count: int = Field(1, ge=1, le=20)
    at: Optional[str] = Field(None, max_length=40)
    source: Optional[str] = Field(None, max_length=10)     # "manual" : entrée manuelle hors ligne
    reason: Optional[str] = Field(None, max_length=200)    # obligatoire si manual
    reentry: bool = False                                  # séances : retour accepté par l'agent


class SyncIn(BaseModel):
    device_id: Optional[str] = Field(None, max_length=64)
    items: List[SyncItem] = Field(..., max_length=500)


class OwnerIn(BaseModel):
    owner_id: Optional[int] = None      # commercial actif ; None = maison (administration)


class SignupSettingsIn(BaseModel):
    """Inscription libre : réglages de la page publique /r/<slug> (back-office)."""
    open: bool = False
    slug: Optional[str] = Field(None, max_length=40)
    category: Optional[str] = Field(None, max_length=40)
    max: Optional[int] = Field(None, ge=1, le=5000)
    until: Optional[datetime] = None


class SignupIn(BaseModel):
    """Inscription libre : formulaire public (sans compte)."""
    full_name: str = Field(..., min_length=2, max_length=120)
    phone: str = Field(..., min_length=8, max_length=30)
    consent: bool = False
    website: Optional[str] = Field(None, max_length=200)   # champ piège (invisible) : rempli = robot


class SessionIn(BaseModel):
    id: Optional[int] = None
    label: str = Field(..., min_length=1, max_length=60)
    starts_at: datetime
    ends_at: datetime
    doors_before_min: Optional[int] = Field(None, ge=0, le=240)


class SessionsIn(BaseModel):
    """Séances d'un événement privé (liste complète : remplace l'existante)."""
    sessions: List[SessionIn] = Field(..., max_length=14)


class CoverIn(BaseModel):
    """Affiche d'un événement privé : data:image/(jpeg|png|webp);base64,… (compressée côté navigateur)."""
    image: str = Field(..., min_length=30, max_length=6_000_000)


class PageIn(BaseModel):
    """Description PUBLIQUE de la page d'inscription (distincte de la note interne)."""
    description: Optional[str] = Field(None, max_length=2000)


class DesignPut(BaseModel):
    """Fiche design d'un événement (lot 6). Tout est optionnel : PUT partiel.
    `fiche` et `theme_override` sont des dicts typés côté cadre ; on borne la
    taille (uploads = données non fiables) et on valide le statut."""
    modele_id: Optional[int] = None
    fiche: Optional[dict] = None
    theme_override: Optional[dict] = None     # palette ajustée (depuis l'affiche ou à la main)
    vip: Optional[dict] = None
    status: Optional[str] = Field(None, pattern="^(brouillon|pret)$")
