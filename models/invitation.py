"""models/invitation.py — Invitations digitales (événements privés).

Un événement privé est un Event avec visibility = "private" (agents, scanner,
date, lieu réutilisés). Les invités sont VOLONTAIREMENT hors de `tickets` :
aucune interaction possible avec paiements, commissions, remboursements,
seats_sold, cagnotte ou export d'audience, et la purge à J+30 ne touche
aucune donnée financière.

PrivateEventSettings : réglages 1-1 de l'événement privé (client, message, suivi).
EventGuest           : un pass nominatif (1 à 20 places), jeton aléatoire.
GuestCheckin         : journal des entrées (en ligne, hors ligne, manuelle).
InvitationPack       : pack payé (taille × niveau de service), activé par l'admin.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)

from core.database import Base


class PrivateEventSettings(Base):
    __tablename__ = "private_event_settings"
    event_id         = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    client_name      = Column(String(120), nullable=False)
    client_phone     = Column(String(20), nullable=True)
    host_label       = Column(String(120), nullable=True)    # « La famille X vous invite »
    message_template = Column(Text, nullable=True)           # None = message par défaut
    service_level    = Column(String(20), nullable=False, default="accompagne")  # autonome | accompagne
    track_token      = Column(String(64), nullable=True, unique=True, index=True)  # lien de suivi client
    track_created_at = Column(DateTime, nullable=True)
    purge_after      = Column(DateTime, nullable=False)       # date de l'événement + 30 j
    purged_at        = Column(DateTime, nullable=True)
    created_by       = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)


class EventGuest(Base):
    __tablename__ = "event_guests"
    id               = Column(Integer, primary_key=True, index=True)
    event_id         = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    token            = Column(String(64), nullable=True, unique=True, index=True)       # None après purge
    token_hash       = Column(String(64), nullable=True, unique=True, index=True)       # sha256 (hors ligne)
    full_name        = Column(String(120), nullable=False)
    phone            = Column(String(20), nullable=True)                                # E.164
    category         = Column(String(40), nullable=True)
    table_label      = Column(String(40), nullable=True)
    seats            = Column(Integer, nullable=False, default=1)
    seats_used       = Column(Integer, nullable=False, default=0)
    revoked_at       = Column(DateTime, nullable=True)
    sent_at          = Column(DateTime, nullable=True)
    sent_by          = Column(Integer, ForeignKey("users.id"), nullable=True)
    first_checkin_at = Column(DateTime, nullable=True)
    last_checkin_at  = Column(DateTime, nullable=True)
    purged           = Column(Boolean, nullable=False, default=False)
    # Pass papier (imprimé, remis en main propre) : par défaut quand l'invité n'a pas de numéro
    paper            = Column(Boolean, nullable=False, default=False, server_default="false")
    # Contrôle manuel hors ligne : empreinte PBKDF2 (sel par invité) des 4 derniers chiffres
    # du téléphone. Seule l'empreinte part dans la liste hors ligne, jamais le numéro.
    phone4_salt      = Column(String(32), nullable=True)
    phone4_hash      = Column(String(64), nullable=True)
    created_by       = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_event_guests_event_phone", "event_id", "phone"),)


class GuestCheckin(Base):
    __tablename__ = "guest_checkins"
    id          = Column(Integer, primary_key=True, index=True)
    guest_id    = Column(Integer, ForeignKey("event_guests.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id    = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    count       = Column(Integer, nullable=False)
    source      = Column(String(10), nullable=False)          # online | offline | manual
    client_uuid = Column(String(40), nullable=True, unique=True)  # idempotence de la synchro hors ligne
    scanned_at  = Column(DateTime, nullable=False)              # heure de l'appareil (UTC)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    by_user_id  = Column(Integer, ForeignKey("users.id"), nullable=True)
    device_id   = Column(String(64), nullable=True)
    conflict    = Column(Boolean, nullable=False, default=False)  # places en trop (2 téléphones hors ligne)
    reason      = Column(String(200), nullable=True)              # entrée manuelle : motif


class InvitationPack(Base):
    __tablename__ = "invitation_packs"
    id                        = Column(Integer, primary_key=True, index=True)
    event_id                  = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    size                      = Column(Integer, nullable=False)        # 50 | 100 | 300 | 500 (personnes)
    service                   = Column(String(20), nullable=False)     # autonome | accompagne
    amount_fcfa               = Column(Integer, nullable=False)        # tarif en vigueur figé à l'activation
    status                    = Column(String(10), nullable=False, default="actif")  # actif | annule
    payment_channel           = Column(String(40), nullable=False)
    payment_ref               = Column(String(80), nullable=False)
    amount_received           = Column(Integer, nullable=False)
    confirmed_goevent_account = Column(Boolean, nullable=False, default=False)
    activated_by              = Column(Integer, ForeignKey("users.id"), nullable=False)
    activated_at              = Column(DateTime, default=datetime.utcnow, nullable=False)
    cancelled_by              = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancelled_at              = Column(DateTime, nullable=True)
    cancel_reason             = Column(String(300), nullable=True)
    note                      = Column(String(300), nullable=True)

    # Un même paiement (canal + référence) ne peut activer qu'un seul pack, même annulé.
    __table_args__ = (UniqueConstraint("payment_channel", "payment_ref", name="uq_invitation_pack_payment"),)
