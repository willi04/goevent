"""
core/event_categories.py — Catégories d'événements publics (liste fermée, lot 5).

Une seule liste pour le formulaire organisateur, l'API et la page Événements.
Les variantes sans accent ou de casse différente (« theatre », « Soiree ») sont
ramenées au libellé canonique ; une valeur inconnue est refusée.
« Privé » est réservée aux événements privés (invitations), jamais saisie.
"""
import re
import unicodedata

CATEGORIES = (
    "Concert", "Festival", "Soirée", "Fête", "Humour", "Théâtre", "Sport", "Cinéma",
    "Conférence", "Formation", "Salon", "Gala", "Gastronomie", "Culturel", "Exposition",
    "Religieux", "Gospel", "Famille", "Business", "Networking", "Mode", "Caritatif", "Autre",
)
PRIVATE_CATEGORY = "Privé"


def _key(value: str) -> str:
    v = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", v.lower())


_INDEX = {_key(c): c for c in CATEGORIES}


def canonical(value):
    """Libellé canonique, ou None si la valeur n'est pas dans la liste."""
    return _INDEX.get(_key(value)) if value else None


def normalize_category(value: str) -> str:
    """Validation d'entrée (schémas) : libellé canonique ou ValueError."""
    c = canonical(value)
    if not c:
        raise ValueError("Catégorie inconnue")
    return c


def variants(label: str) -> set:
    """Valeurs stockées équivalentes (avant rattrapage SQL) : « Théâtre » → {Théâtre, Theatre}."""
    plain = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    return {label, plain}
