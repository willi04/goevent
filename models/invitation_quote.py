"""
models/invitation_quote.py — Devis numérotés de l'offre Invitations (lot 4.5).

Un devis peut exister sans événement (premier contact d'un prospect). Il garde
un instantané complet du calcul (même calcul que l'activation, core.invitation_catalog.price) :
modifier le catalogue ne le change jamais. Numéro DEV-AAAA-NNNN attribué par le
serveur (compteur par année, même transaction que l'insertion : ni trou ni doublon).

Propriétaire (owner_id) = commercial du dossier : un commercial ne voit que ses
devis ; l'admin voit tout et peut réaffecter. Statut « expiré » calculé à la
lecture (valid_until dépassée pour un brouillon / envoyé).
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from core.database import Base


class InvitationQuoteCounter(Base):
    __tablename__ = "invitation_quote_counters"
    year = Column(Integer, primary_key=True)
    last = Column(Integer, nullable=False, default=0)


class InvitationQuote(Base):
    __tablename__ = "invitation_quotes"
    id               = Column(Integer, primary_key=True, index=True)
    number           = Column(String(20), nullable=False, unique=True)     # DEV-2026-0001
    year             = Column(Integer, nullable=False)
    seq              = Column(Integer, nullable=False)
    owner_id         = Column(Integer, ForeignKey("users.id"), nullable=False)
    sales_code       = Column(String(20), nullable=True)                   # copie figée du code du propriétaire
    status           = Column(String(12), nullable=False, default="brouillon")  # brouillon|envoye|accepte|refuse|converti
    # Client
    client_name      = Column(String(120), nullable=False)
    client_company   = Column(String(120), nullable=True)
    client_phone     = Column(String(20), nullable=True)                   # E.164
    event_type       = Column(String(60), nullable=True)
    event_date       = Column(Date, nullable=True)
    location         = Column(String(200), nullable=True)
    guests_count     = Column(Integer, nullable=True)
    # Contrôle de doublon (valeurs normalisées)
    phone_norm       = Column(String(20), nullable=True)
    name_norm        = Column(String(160), nullable=True)
    company_norm     = Column(String(160), nullable=True)
    conflict_with_quote_id = Column(Integer, nullable=True)                # conflit forcé par l'admin
    possible_duplicate_of  = Column(Integer, nullable=True)                # alerte (admin seulement)
    # Calcul figé
    formula_id       = Column(Integer, nullable=True)
    snapshot         = Column(JSONB, nullable=False)
    total_fcfa       = Column(Integer, nullable=False, default=0)          # total à payer (TTC)
    pack_amount_fcfa = Column(Integer, nullable=False, default=0)          # base de commission prévisionnelle
    needs_admin      = Column(Boolean, nullable=False, default=False)      # sur mesure / remise manuelle à valider
    validated_by     = Column(Integer, ForeignKey("users.id"), nullable=True)
    validated_at     = Column(DateTime, nullable=True)
    # Cycle de vie
    valid_until      = Column(Date, nullable=False)                        # émission + 15 jours (jour de Bangui)
    sent_at          = Column(DateTime, nullable=True)
    accepted_at      = Column(DateTime, nullable=True)
    refused_at       = Column(DateTime, nullable=True)
    refused_reason   = Column(String(300), nullable=True)
    event_id         = Column(Integer, ForeignKey("events.id"), nullable=True)
    pack_id          = Column(Integer, nullable=True)
    converted_at     = Column(DateTime, nullable=True)
    # Données personnelles des prospects (non convertis) : anonymisées à purge_after
    purge_after      = Column(Date, nullable=False)
    purged_at        = Column(DateTime, nullable=True)
    created_by       = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at       = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_quotes_owner_status", "owner_id", "status"),
        Index("ix_quotes_phone_norm", "phone_norm"),
        Index("ix_quotes_event_date", "event_date"),
    )
