"""models/invitation_catalog.py — Catalogue des packs Invitations (lot 2.2).

Géré par l'admin (back-office → Invitations → Catalogue), lu par l'activation,
la page publique (lot 3) et le tableau de bord organisateur (V2) : affiché = facturé.

InvFormula      : formule (niveau de service) et ses comportements techniques.
InvPack         : pack = taille (personnes) × formule, prix ou « sur devis ».
InvOption       : supplément (forfait / par unité / par page), lié à des formules.
InvDiscountCode : code de remise (sur le PACK uniquement).
InvCatalogLog   : historique des modifications (qui, quand, ancien → nouveau).

Une commande activée (invitation_packs) garde un INSTANTANÉ : modifier ou
archiver le catalogue ne la change jamais. Élément déjà utilisé (used = true) :
archivable, jamais supprimable.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Table, text
from sqlalchemy.dialects.postgresql import JSONB

from core.database import Base

inv_option_formulas = Table(
    "inv_option_formulas", Base.metadata,
    Column("option_id", Integer, ForeignKey("inv_options.id", ondelete="CASCADE"), primary_key=True),
    Column("formula_id", Integer, ForeignKey("inv_formulas.id", ondelete="CASCADE"), primary_key=True),
)


class _Audit:
    status     = Column(String(10), nullable=False, default="active")   # active | archived
    used       = Column(Boolean, nullable=False, default=False)          # déjà dans une commande
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class InvFormula(_Audit, Base):
    __tablename__ = "inv_formulas"
    id            = Column(Integer, primary_key=True)
    code          = Column(String(20), nullable=False, unique=True)   # immuable (autonome, accompagne…)
    name          = Column(String(60), nullable=False)
    description   = Column(String(300), nullable=True)
    included      = Column(JSONB, nullable=False, default=list)       # puces « inclus »
    badge         = Column(String(30), nullable=True)                 # « Populaire »
    public        = Column(Boolean, nullable=False, default=True)
    onsite_agents = Column(Boolean, nullable=False, default=False)    # agents GoEvent sur place
    track_link    = Column(Boolean, nullable=False, default=False)    # lien de suivi client
    team_sends    = Column(Boolean, nullable=False, default=False)    # envoi des pass par l'équipe GoEvent
    agents_step   = Column(Integer, nullable=True)                    # 1 agent par tranche de N invités


class InvPack(_Audit, Base):
    __tablename__ = "inv_packs"
    id          = Column(Integer, primary_key=True)
    formula_id  = Column(Integer, ForeignKey("inv_formulas.id"), nullable=False, index=True)
    size        = Column(Integer, nullable=False)                     # personnes (1 à 5 000)
    price_fcfa  = Column(Integer, nullable=True)                      # NULL si sur devis
    on_quote    = Column(Boolean, nullable=False, default=False)
    name        = Column(String(60), nullable=True)                   # « Petit comité »
    public      = Column(Boolean, nullable=False, default=True)
    valid_from  = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)

    # Pas de doublon taille × formule ACTIF (les archivés restent pour l'historique)
    __table_args__ = (Index("uq_inv_packs_active_size", "formula_id", "size", unique=True,
                            postgresql_where=text("status = 'active'")),)


class InvOption(_Audit, Base):
    __tablename__ = "inv_options"
    id              = Column(Integer, primary_key=True)
    name            = Column(String(80), nullable=False)
    description     = Column(String(300), nullable=True)
    price_fcfa      = Column(Integer, nullable=False)
    unit            = Column(String(10), nullable=False, default="flat")   # flat | per_unit | per_page
    unit_label      = Column(String(30), nullable=True)                    # « agent / soirée »
    max_qty         = Column(Integer, nullable=False, default=1)
    agents_per_unit = Column(Integer, nullable=False, default=0)           # « agent supplémentaire » : 1
    public          = Column(Boolean, nullable=False, default=True)


class InvDiscountCode(_Audit, Base):
    __tablename__ = "inv_discount_codes"
    id          = Column(Integer, primary_key=True)
    code        = Column(String(40), nullable=False, unique=True)     # majuscules
    kind        = Column(String(10), nullable=False)                  # pct | fixed
    value       = Column(Integer, nullable=False)                     # % (1-100) ou FCFA
    max_uses    = Column(Integer, nullable=True)                      # NULL = illimité
    valid_from  = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)
    formula_ids = Column(JSONB, nullable=False, default=list)         # vide = toutes
    pack_ids    = Column(JSONB, nullable=False, default=list)         # vide = tous
    note        = Column(String(200), nullable=True)


class InvCatalogLog(Base):
    __tablename__ = "inv_catalog_log"
    id        = Column(Integer, primary_key=True)
    entity    = Column(String(20), nullable=False)    # formula | pack | option | discount | settings
    entity_id = Column(Integer, nullable=True)
    action    = Column(String(10), nullable=False)    # create | update | archive | restore | delete | seed
    label     = Column(String(120), nullable=True)    # nom lisible au moment du changement
    changes   = Column(JSONB, nullable=False, default=dict)   # {champ: [ancien, nouveau]}
    user_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    at        = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (Index("ix_inv_catalog_log_entity", "entity", "entity_id"),)
