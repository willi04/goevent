"""schemas/audience.py — Pydantic v2 (Chantier 19.B)"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class AudienceContactOut(BaseModel):
    """1 ligne de la vue audience (1 ticket = 1 ligne)."""
    ticket_id: int
    user_id: int
    nom: str
    telephone: Optional[str]
    email: Optional[str]
    event_id: int
    event_titre: str
    event_date: Optional[datetime]
    categorie: Optional[str]
    prix_paye_fcfa: float
    statut: str
    methode_paiement: Optional[str]
    date_achat: datetime
    segments: List[str]


class AudienceListOut(BaseModel):
    """Réponse paginée pour le listing audience."""
    contacts: List[AudienceContactOut]
    total: int
    offset: int
    limit: int
    has_more: bool


class SegmentStatsOut(BaseModel):
    """Stats globales des 3 segments."""
    recurrent: int
    premium: int
    dormant: int
    total_unique_buyers: int


class AudienceStatsOut(BaseModel):
    """Stats globales audience (header de la page)."""
    total_contacts: int
    total_ca_fcfa: float
    nb_events: int
    segments: SegmentStatsOut
