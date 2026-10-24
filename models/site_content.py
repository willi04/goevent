"""models/site_content.py — Contenu éditable du site (back-office CMS).

SiteContent       : surcharges clé → valeur des textes/images/liens balisés
                    data-cms* dans web/ (la valeur par défaut reste dans le HTML).
SitePageRevision  : pages légales en HTML riche, append-only (historique daté).
FaqItem           : questions/réponses du centre d'aide.
ZoneBlock         : blocs (affiches, bannières, annonces, cartes) des emplacements.
SiteSetting       : réglages JSON (tarifs, comportement des emplacements).
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text

from core.database import Base


class SiteContent(Base):
    __tablename__ = "site_contents"
    key        = Column(String(120), primary_key=True)
    value      = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class SitePageRevision(Base):
    """Une ligne = une version publiée. La version courante = id max du slug."""
    __tablename__ = "site_page_revisions"
    id         = Column(Integer, primary_key=True, index=True)
    slug       = Column(String(40), nullable=False)
    title      = Column(String(200), nullable=False)
    body_html  = Column(Text, nullable=False)
    note       = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (Index("ix_site_page_revisions_slug_id", "slug", "id"),)


class FaqItem(Base):
    __tablename__ = "faq_items"
    id          = Column(Integer, primary_key=True, index=True)
    category    = Column(String(20), nullable=False, index=True)
    question    = Column(String(300), nullable=False)
    answer_html = Column(Text, nullable=False)
    position    = Column(Integer, default=0, nullable=False)
    is_active   = Column(Boolean, default=True, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_by  = Column(Integer, ForeignKey("users.id"), nullable=True)


class ZoneBlock(Base):
    """Bloc affiché dans un emplacement (zone) des pages publiques.

    kind : image (affiche) | banner (bannière partenaire) | announcement | cards.
    Zones et types autorisés : core/zones.py. Rotation / empilement selon la zone.
    """
    __tablename__ = "zone_blocks"
    id          = Column(Integer, primary_key=True, index=True)
    zone        = Column(String(60), nullable=False, index=True)
    kind        = Column(String(20), nullable=False)
    title       = Column(String(160), nullable=True)
    body        = Column(Text, nullable=True)
    image       = Column(Text, nullable=True)          # data URL (PNG/JPEG/WebP) ou https
    link_url    = Column(String(500), nullable=True)
    link_label  = Column(String(60), nullable=True)
    sponsor     = Column(String(120), nullable=True)   # affiche « Sponsorisé · … »
    theme       = Column(String(20), default="green", nullable=False)
    items       = Column(Text, nullable=True)          # JSON : cartes [{icon, title, text}]
    position    = Column(Integer, default=0, nullable=False)
    is_active   = Column(Boolean, default=True, nullable=False)
    starts_at   = Column(DateTime, nullable=True)
    ends_at     = Column(DateTime, nullable=True)
    impressions = Column(Integer, default=0, nullable=False)
    clicks      = Column(Integer, default=0, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_by  = Column(Integer, ForeignKey("users.id"), nullable=True)


class SiteSetting(Base):
    """Réglages JSON du site : « pricing » (tarifs), « zones » (comportement des emplacements)."""
    __tablename__ = "site_settings"
    key        = Column(String(40), primary_key=True)
    value      = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
