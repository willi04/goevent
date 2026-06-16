"""
core/permissions.py
Référentiel central des rôles, sous-fonctions staff, et helpers de garde
pour les routes FastAPI. Toute logique de permission passe par ce module.
"""
from typing import Optional
from fastapi import Depends, HTTPException

from core.security import get_current_user
from models.user import User


# ════════════════════════════════════════════════════════════
# RÉFÉRENTIEL DES RÔLES
# ════════════════════════════════════════════════════════════

ROLES_VALIDES = (
    "fan",        # acheteur de billets
    "organizer",  # créateur d'événements
    "agent",      # scanner d'un événement (créé par un organizer)
    "staff",      # employé GoEvent (commercial, cm, designer)
    "admin",      # super-admin
    "partner",    # réservé futur (ex: annonceurs externes)
)

# Rôles ayant accès aux pages publiques organisateur (création event, payouts...)
ROLES_ORGANIZER_LIKE = ("organizer",)

# Rôles considérés "internes GoEvent" (peuvent voir le back-office)
ROLES_INTERNAL = ("admin", "staff")

# Sous-fonctions valides pour role='staff'
STAFF_FUNCTIONS_VALIDES = (
    "commercial",  # responsable commercial & opérations
    "cm",          # community manager / traffic manager
    "designer",    # designer / responsable régie publicitaire
)

# Mapping de compatibilité — comptes legacy avec des rôles fantômes
# Ces rôles n'apparaissent JAMAIS à la création (signup filtre), mais peuvent
# exister dans la BD à cause d'anciens scripts/imports manuels. On les normalise
# en lecture pour ne pas casser leur session existante.
ROLE_ALIAS = {
    "artiste": "organizer",
    "organisation": "organizer",
    # 'partner' n'est PAS aliasé — c'est un rôle valide réservé pour le futur
}


def normalize_role(role: Optional[str]) -> str:
    """
    Retourne le rôle canonique. Si le rôle BD est un alias legacy, le mappe.
    Si le rôle est inconnu, retourne 'fan' (sécurité par défaut, accès minimal).
    """
    if not role:
        return "fan"
    canonical = ROLE_ALIAS.get(role, role)
    return canonical if canonical in ROLES_VALIDES else "fan"


# ════════════════════════════════════════════════════════════
# HELPERS DE GARDE — utilisables en Depends() FastAPI
# ════════════════════════════════════════════════════════════

def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """
    Garde stricte : admin uniquement.
    Utilise pour les actions hyper-sensibles (suppression compte, audit global).
    """
    if normalize_role(user.role) != "admin":
        raise HTTPException(403, "Réservé aux administrateurs")
    return user


def get_current_admin_or_staff(user: User = Depends(get_current_user)) -> User:
    """
    Admin OU n'importe quel staff (toutes sous-fonctions).
    Utilise pour les routes back-office largement partagées
    (lecture stats, voir événements, voir feedbacks).
    """
    if normalize_role(user.role) not in ROLES_INTERNAL:
        raise HTTPException(403, "Accès réservé à l'équipe GoEvent")
    return user


def require_staff_function(*allowed_functions: str):
    """
    Garde paramétrable : admin OU staff avec une sous-fonction précise.
    Utilise en factory dans le Depends :
        Depends(require_staff_function("commercial"))
        Depends(require_staff_function("commercial", "cm"))
    Admin a toujours accès.
    """
    for fn in allowed_functions:
        if fn not in STAFF_FUNCTIONS_VALIDES:
            raise ValueError(f"Fonction staff invalide : {fn}")

    def _checker(user: User = Depends(get_current_user)) -> User:
        role = normalize_role(user.role)
        if role == "admin":
            return user
        if role == "staff" and user.staff_function in allowed_functions:
            return user
        raise HTTPException(
            403,
            f"Réservé à l'équipe GoEvent (fonctions : {', '.join(allowed_functions)})",
        )

    return _checker


def is_admin(user: User) -> bool:
    """Helper non-Depends pour usage dans le corps des routes."""
    return normalize_role(user.role) == "admin"


def is_admin_or_staff(user: User, function: Optional[str] = None) -> bool:
    """
    Helper non-Depends pour vérifications conditionnelles dans le code.
    Si `function` est précisé, vérifie aussi la sous-fonction.
    """
    role = normalize_role(user.role)
    if role == "admin":
        return True
    if role == "staff":
        if function is None:
            return True
        return user.staff_function == function
    return False
