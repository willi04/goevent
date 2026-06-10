"""
utils/slugs.py
Encodage réversible ID événement <=> slug court de 8 caractères.
Sert aux liens partagés : goevent.africa/e/aB3xK9mP
Le salt secret rend les hash imprévisibles (on ne devine pas l'ID 6 depuis l'ID 5).
"""
from typing import Optional
from hashids import Hashids

from core.config import settings

hashids = Hashids(
    salt=settings.HASHIDS_SALT,
    min_length=settings.HASHIDS_MIN_LENGTH,
    alphabet=settings.HASHIDS_ALPHABET,
)


def event_id_to_slug(event_id: int) -> str:
    """Convertit un ID d'événement en slug court. Ex: 5 -> 'aB3xK9mP'"""
    return hashids.encode(event_id)


def slug_to_event_id(slug: str) -> Optional[int]:
    """Décode un slug et renvoie l'ID d'événement, ou None si invalide."""
    decoded = hashids.decode(slug)
    return decoded[0] if decoded else None
