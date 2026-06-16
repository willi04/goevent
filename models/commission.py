"""
models/commission.py — Table polymorphe des commissions à verser aux staff.

Une ligne par commission générée par un événement métier.
Chaque vente de billet, contrat pub, boost visibilité ou pack FB ads
crée une ligne ici, attribuée au staff qui en est la source.

Le calcul automatique des commissions sera fait via triggers applicatifs
dans le Chantier Commissions (commits 10.x), pas dans ce commit.
Cette table est juste la fondation BD.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from core.database import Base


class Commission(Base):
    __tablename__ = "commissions"

    id = Column(Integer, primary_key=True, index=True)

    # Qui reçoit la commission (staff GoEvent)
    beneficiary_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Source polymorphe : quel événement métier a généré cette commission
    # source_type ∈ {'ticket', 'ad_contract', 'boost_visibility', 'fb_ads_pack'}
    # source_id = id de la table correspondante (tickets.id, ad_contracts.id, etc.)
    # NB : pas de FK contrainte car polymorphe — l'intégrité est gérée applicativement.
    source_type = Column(String(50), nullable=False, index=True)
    source_id   = Column(Integer, nullable=False)

    # Montants
    base_amount_fcfa   = Column(Float, nullable=False)  # montant brut sur lequel se calcule la commission
    rate_applied       = Column(Float, nullable=False)  # taux appliqué (ex: 0.25 = 25%)
    amount_fcfa        = Column(Float, nullable=False)  # commission effective = base × rate

    # Cycle de vie
    # status ∈ {'pending', 'approved', 'paid', 'cancelled'}
    # - pending  : générée automatiquement, en attente de validation admin
    # - approved : validée par admin, prête à être payée
    # - paid     : virement effectué (Orange Money manuel pour MVP)
    # - cancelled: annulée (ex: vente remboursée, contrat résilié)
    status = Column(String(20), default="pending", nullable=False, index=True)

    # Métadonnées
    note         = Column(Text, nullable=True)            # commentaire admin libre
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    approved_at  = Column(DateTime, nullable=True)
    approved_by  = Column(Integer, ForeignKey("users.id"), nullable=True)
    paid_at      = Column(DateTime, nullable=True)
    paid_by      = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relations
    beneficiary = relationship("User", foreign_keys=[beneficiary_user_id])
    approver    = relationship("User", foreign_keys=[approved_by])
    payer       = relationship("User", foreign_keys=[paid_by])

    # Index composite pour la requête fréquente : "commissions d'un user par statut"
    __table_args__ = (
        Index("idx_commission_user_status", "beneficiary_user_id", "status"),
        Index("idx_commission_source", "source_type", "source_id"),
    )
