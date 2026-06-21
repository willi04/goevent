"""
utils/promo.py — Verrouillage + calculs pour codes promo (Chantier 19.A).

Calque la philosophie de utils/locks.py :
  - SELECT ... FOR UPDATE pour incrémenter used_count atomiquement
  - Revalide les 7 checks à l'intérieur du verrou (anti-race condition)
  - HTTPException claires propagées au front via bt-api.js

Source de vérité : utilisée par initiate-ngser et reserve-cash.
"""
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.promo import PromoCode, PromoUsage


def find_active_promo(
    db: Session,
    organizer_id: int,
    code: str,
    event_id: int,
    tier_name: Optional[str] = None,
) -> PromoCode:
    """
    Recherche LECTURE SEULE (pas de verrou). Utilisée par la route publique
    /promo-codes/validate pour preview UX. Lève HTTPException sur tous les
    cas d'invalidité avec un message orienté utilisateur final.
    """
    promo = (
        db.query(PromoCode)
        .filter(
            PromoCode.organizer_id == organizer_id,
            func.lower(PromoCode.code) == code.strip().lower(),
        )
        .first()
    )
    if not promo:
        raise HTTPException(404, "Code promo inconnu")

    _assert_promo_applicable(promo, event_id, tier_name)
    return promo


def lock_promo_for_use(
    db: Session,
    user_id: int,
    event_id: int,
    tier_name: Optional[str],
    code: str,
    organizer_id: int,
) -> PromoCode:
    """
    Verrou pessimiste + revalidation atomique des 7 checks.
    À utiliser dans initiate-ngser / reserve-cash JUSTE AVANT l'incrément
    used_count et la création de la PromoUsage.

    Lève :
      404 si le code n'existe pas pour cette orga
      410 si désactivé / expiré
      400 si event/tier incompatible
      409 si épuisé (global ou par user)
    """
    promo = (
        db.query(PromoCode)
        .filter(
            PromoCode.organizer_id == organizer_id,
            func.lower(PromoCode.code) == code.strip().lower(),
        )
        .with_for_update()
        .first()
    )
    if not promo:
        raise HTTPException(404, "Code promo inconnu")

    _assert_promo_applicable(promo, event_id, tier_name)

    # Quota global (revalidé à l'intérieur du verrou)
    if promo.max_uses_global is not None and promo.used_count >= promo.max_uses_global:
        raise HTTPException(409, "Ce code promo est épuisé")

    # Quota par user : count des usages reserved/confirmed (pas released)
    used_by_user = (
        db.query(func.count(PromoUsage.id))
        .filter(
            PromoUsage.promo_id == promo.id,
            PromoUsage.user_id == user_id,
            PromoUsage.status.in_(("reserved", "confirmed")),
        )
        .scalar()
    ) or 0
    if used_by_user >= promo.max_uses_per_user:
        raise HTTPException(409, "Vous avez déjà utilisé ce code promo")

    return promo


def _assert_promo_applicable(
    promo: PromoCode,
    event_id: int,
    tier_name: Optional[str],
) -> None:
    """Checks d'applicabilité communs. Lève HTTPException."""
    if not promo.is_active:
        raise HTTPException(410, "Code promo désactivé")

    now = datetime.utcnow()
    if promo.starts_at and promo.starts_at > now:
        raise HTTPException(410, "Ce code n'est pas encore actif")
    if promo.ends_at and promo.ends_at < now:
        raise HTTPException(410, "Code promo expiré")

    if promo.event_id is not None and promo.event_id != event_id:
        raise HTTPException(400, "Code non applicable à cet événement")

    if promo.tier_name and tier_name and promo.tier_name != tier_name:
        raise HTTPException(400, "Code non applicable à cette catégorie")


def compute_discount(promo: PromoCode, prix_base: float) -> int:
    """
    Calcule le montant FCFA déduit. Arrondi à l'entier inférieur (jamais de centimes).
    S'applique sur prix_base UNIQUEMENT (les 4% frais GoEvent ne sont jamais réduits).
    Le résultat est plafonné à prix_base (jamais de prix négatif).
    """
    if promo.discount_type == "percent":
        raw = float(prix_base) * float(promo.discount_value) / 100.0
    else:  # fixed
        raw = float(promo.discount_value)
    discount = int(min(raw, float(prix_base)))
    return max(0, discount)


def confirm_promo_usage(db: Session, ticket_id: int) -> None:
    """
    Webhook NGSER status_id=1 : passe les usages 'reserved' → 'confirmed'.
    Définitif, pas de décrément used_count.
    """
    db.query(PromoUsage).filter(
        PromoUsage.ticket_id == ticket_id,
        PromoUsage.status == "reserved",
    ).update({"status": "confirmed"}, synchronize_session=False)


def release_promo_usage(db: Session, ticket_id: int) -> None:
    """
    Échec paiement / cron purge / cash annulé :
      - passe les usages 'reserved' → 'released'
      - décrémente used_count du code promo associé (avec verrou)
    Idempotent : si déjà released ou confirmed, no-op.
    """
    usages = (
        db.query(PromoUsage)
        .filter(
            PromoUsage.ticket_id == ticket_id,
            PromoUsage.status == "reserved",
        )
        .all()
    )
    for u in usages:
        promo = (
            db.query(PromoCode)
            .filter(PromoCode.id == u.promo_id)
            .with_for_update()
            .first()
        )
        if promo and promo.used_count > 0:
            promo.used_count -= 1
        u.status = "released"
