"""utils/locks.py — Verrouillage pessimiste sur ressources critiques.

Centralise les SELECT ... FOR UPDATE pour les opérations qui touchent à un
compteur partagé (typiquement events.seats_sold). Garantit qu'aucun autre
worker FastAPI ne peut lire/modifier la ligne entre le check et l'écriture,
même avec 5000 requêtes concurrentes sur Neon.

Le verrou est relâché à la fin de la transaction (commit ou rollback).
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session
from models.event import Event


def lock_event_for_booking(db: Session, event_id: int) -> Event:
    """
    Verrou pessimiste + check quota AVANT d'incrémenter seats_sold.
    À utiliser pour : reserve_ticket_cash, initiate_ngser_payment.

    Lève :
      404 si l'événement n'existe pas ou est inactif
      409 si l'événement est complet (le frontend doit afficher un message
          "plus de places disponibles" plutôt qu'une erreur générique)
    """
    event = (
        db.query(Event)
        # visibility : un événement privé (invitations) n'est jamais vendu ni réservable
        .filter(Event.id == event_id, Event.is_active == True, Event.visibility == "public")
        .with_for_update()
        .first()
    )
    if not event:
        raise HTTPException(404, "Événement introuvable")
    if event.seats_sold >= event.total_seats:
        raise HTTPException(409, "Plus de places disponibles")
    return event


def lock_event_for_release(db: Session, event_id: int) -> Event | None:
    """
    Verrou pessimiste pour DÉCRÉMENTER seats_sold (annulation, libération
    sur échec de paiement). Pas de check quota.
    Retourne None si l'event a été supprimé entre temps (cas marginal).
    """
    return (
        db.query(Event)
        .filter(Event.id == event_id)
        .with_for_update()
        .first()
    )
