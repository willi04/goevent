"""models/invitation.py — Invitations digitales (événements privés).

Un événement privé est un Event avec visibility = "private" (agents, scanner,
date, lieu réutilisés). Les invités sont VOLONTAIREMENT hors de `tickets` :
aucune interaction possible avec paiements, commissions, remboursements,
seats_sold, cagnotte ou export d'audience, et la purge à J+30 ne touche
aucune donnée financière.

PrivateEventSettings : réglages 1-1 de l'événement privé (client, message, suivi).
EventGuest           : un pass nominatif (1 à 20 places), jeton aléatoire.
GuestCheckin         : journal des entrées (en ligne, hors ligne, manuelle).
EventSession         : séances d'un événement sur plusieurs jours (une entrée par séance).
InvitationPage       : présentation publique (affiche, description) de la page d'inscription.
InvitationPack       : pack payé (taille × niveau de service), activé par l'admin.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB

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
    # Portefeuille (lot 4.5) : commercial propriétaire ; NULL = maison (admin)
    owner_id         = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    source_quote_id  = Column(Integer, nullable=True)
    # Inscription libre (page publique /r/<slug>, sans compte) : chaque inscrit reçoit
    # immédiatement un pass d'1 place. Plafond = pack actif (toujours) et signup_max (facultatif).
    signup_slug      = Column(String(40), nullable=True)   # unique : index partiel ci-dessous
    signup_open      = Column(Boolean, nullable=False, default=False, server_default="false")
    signup_category  = Column(String(40), nullable=True)       # None = « Participant »
    signup_max       = Column(Integer, nullable=True)          # personnes inscrites en ligne ; None = pack
    signup_until     = Column(DateTime, nullable=True)         # UTC ; None = jusqu'à fermeture manuelle

    __table_args__ = (Index("uq_private_event_settings_signup_slug", "signup_slug", unique=True,
                            postgresql_where=text("signup_slug IS NOT NULL")),)


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
    source           = Column(String(10), nullable=True)   # None = back-office ; « signup » = inscription libre

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
    # Séances (événement sur plusieurs jours) : NULL = événement sans séance (une entrée au total).
    # reentry = retour dans la même séance accepté par l'agent : journalisé, jamais compté comme entrée.
    session_id  = Column(Integer, nullable=True)
    reentry     = Column(Boolean, nullable=False, default=False, server_default="false")


class EventSession(Base):
    """Séance d'un événement privé (« Jour 1 », « Soirée d'ouverture »). Avec des séances, un pass
    donne ses places à CHAQUE séance ; sans séance, il les donne une fois pour tout l'événement."""
    __tablename__ = "event_sessions"
    id               = Column(Integer, primary_key=True, index=True)
    event_id         = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    label            = Column(String(60), nullable=False)
    starts_at        = Column(DateTime, nullable=False)          # UTC
    ends_at          = Column(DateTime, nullable=False)          # UTC
    doors_before_min = Column(Integer, nullable=False, default=60)  # scan accepté N min avant le début
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)


class InvitationPack(Base):
    __tablename__ = "invitation_packs"
    id                        = Column(Integer, primary_key=True, index=True)
    event_id                  = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    size                      = Column(Integer, nullable=False)        # personnes
    service                   = Column(String(20), nullable=False)     # code de la formule (autonome, accompagne…)
    amount_fcfa               = Column(Integer, nullable=False)        # TOTAL à payer (TTC), figé à l'activation
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
    # ── Commande figée (lot 2.2). NULL = pack activé avant le catalogue (« ancien pack »).
    # Pas de FK vers le catalogue : colonnes ajoutées par ALTER avant la création des tables.
    formula_id                = Column(Integer, nullable=True)
    formula_name              = Column(String(80), nullable=True)
    catalog_pack_id           = Column(Integer, nullable=True)
    custom                    = Column(Boolean, nullable=False, default=False, server_default="false")  # sur mesure
    offered                   = Column(Boolean, nullable=False, default=False, server_default="false")  # pack offert
    unit_price                = Column(Integer, nullable=True)        # prix du pack avant remise
    options_total             = Column(Integer, nullable=True)
    discount_kind             = Column(String(10), nullable=True)     # code | manual
    discount_code_id          = Column(Integer, nullable=True)
    discount_amount           = Column(Integer, nullable=True)
    agents_included           = Column(Integer, nullable=True)
    features                  = Column(JSONB, nullable=True)          # {onsite_agents, track_link, team_sends}
    amount_ht                 = Column(Integer, nullable=True)
    amount_vat                = Column(Integer, nullable=True)
    vat_rate_bp               = Column(Integer, nullable=True)        # taux en points de base (1925 = 19,25 %)
    snapshot                  = Column(JSONB, nullable=True)          # détail complet (lignes, motifs, noms)
    # ── Équipe commerciale (lot 4.5) : devis d'origine et commission figés à l'activation
    quote_id                  = Column(Integer, nullable=True)        # invitation_quotes.id (unique, cf. migrations)
    owner_id                  = Column(Integer, ForeignKey("users.id"), nullable=True)  # commercial crédité
    commission_base_fcfa      = Column(Integer, nullable=True)        # pack après remise (HT si TVA)
    commission_rate_bp        = Column(Integer, nullable=True)        # 2500 = 25 %

    # Un même paiement (canal + référence) ne peut activer qu'un seul pack, même annulé.
    __table_args__ = (UniqueConstraint("payment_channel", "payment_ref", name="uq_invitation_pack_payment"),)


class InvitationPage(Base):
    """Présentation PUBLIQUE d'un événement privé : page d'inscription libre et aperçu de partage
    (WhatsApp…). Description publique distincte de events.description (note interne du back-office).
    Table À PART : jamais chargée par les routes chaudes (scan, pass, synchro, suivi) ; l'image est
    ré-encodée en JPEG par le serveur (métadonnées retirées). Lire cover_data seulement pour la servir."""
    __tablename__ = "invitation_pages"
    event_id     = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    description  = Column(Text, nullable=True)
    cover_data   = Column(LargeBinary, nullable=True)      # JPEG
    cover_sha    = Column(String(64), nullable=True)       # version (cache navigateur / CDN)
    cover_width  = Column(Integer, nullable=True)
    cover_height = Column(Integer, nullable=True)
    updated_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


# ══════════════════════════════════════════════════════════════════════════
# Lot 6 — Couche visuelle des invitations (cadres, gabarits, fiche design).
# Tables NEUVES : créées par Base.metadata.create_all() au boot. Aucune
# migration dans core/migrations.py (réservé aux colonnes sur tables
# existantes). Les tiers invités passent par EventGuest.category : AUCUNE
# nouvelle colonne sur une table existante. Zéro impact pass/QR/scan.
# ══════════════════════════════════════════════════════════════════════════

class InvModeleCadre(Base):
    """Layout (structure) d'invitation. Ex : Keynote, Agenda, Médaillon.
    Le rendu ne reconstruit JAMAIS une affiche : on part du cadre, on
    recolorise. `suggest_for` = types d'événement → moteur de suggestion
    (simple lookup, pas d'IA)."""
    __tablename__ = "inv_modele_cadres"
    id           = Column(Integer, primary_key=True, index=True)
    code         = Column(String(40), nullable=False, unique=True, index=True)   # slug immuable (b1, c1…)
    famille      = Column(String(20), nullable=False, index=True)                # familial | corporate
    name         = Column(String(120), nullable=False)
    scenes       = Column(JSONB, nullable=False)   # {enveloppe:bool, page:bool, programme:bool, pass:"pass"|"badge"}
    champs       = Column(JSONB, nullable=False)   # schéma de fiche typé (cf. §3) rendu par l'écran Design
    suggest_for  = Column(JSONB, nullable=True)    # ["mariage","dot"…] | ["conference","salon"…]
    sort_order   = Column(Integer, nullable=False, default=0)
    status       = Column(String(10), nullable=False, default="actif")           # actif | archive
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


class InvModele(Base):
    """Gabarit présenté au catalogue = cadre × skin (palette, polices,
    ornements). Un cadre a plusieurs skins (terracotta, nuit dorée…)."""
    __tablename__ = "inv_modeles"
    id           = Column(Integer, primary_key=True, index=True)
    cadre_id     = Column(Integer, ForeignKey("inv_modele_cadres.id", ondelete="RESTRICT"), nullable=False, index=True)
    famille      = Column(String(20), nullable=False, index=True)
    name         = Column(String(120), nullable=False)
    skin         = Column(JSONB, nullable=False)   # {couleurs{fond,accent,clair,texte}, polices{}, ornements{}}
    preview_url  = Column(String(300), nullable=True)
    enveloppe    = Column(Boolean, nullable=False, default=False, server_default="false")
    public       = Column(Boolean, nullable=False, default=True,  server_default="true")
    sort_order   = Column(Integer, nullable=False, default=0)
    status       = Column(String(10), nullable=False, default="actif")           # actif | archive
    created_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at   = Column(DateTime, nullable=True)


class InvitationDesign(Base):
    """Fiche visuelle d'UN événement privé (1-1). Porte le choix de gabarit,
    la palette ajustée, la/les photo(s) du client et le contenu typé.
    Cloisonnée par organisateur (owner_id). Purgée avec l'événement."""
    __tablename__ = "invitation_design"
    event_id       = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    modele_id      = Column(Integer, ForeignKey("inv_modeles.id", ondelete="SET NULL"), nullable=True, index=True)
    theme_override = Column(JSONB, nullable=True)      # palette extraite de l'affiche + ajustements admin
    photo_url      = Column(String(300), nullable=True)   # photo client — SON invitation uniquement
    photo_crop     = Column(JSONB, nullable=True)      # {zoom, x, y}
    logo_url       = Column(String(300), nullable=True)   # logo corporate (asset, jamais reconstruit)
    fiche          = Column(JSONB, nullable=True)      # contenu typé selon cadre.champs (cf. §3)
    vip            = Column(JSONB, nullable=True)      # {salon_label, acces_prioritaire} (corporate)
    # Photo client / logo : image uploadée RÉ-ENCODÉE (Pillow), métadonnées retirées, servie
    # derrière le pass_token (jamais énumérable). Octets stockés en base comme InvitationPage.cover.
    photo_data     = Column(LargeBinary, nullable=True)
    photo_mime     = Column(String(40), nullable=True)
    photo_sha      = Column(String(64), nullable=True)    # version (cache navigateur)
    logo_data      = Column(LargeBinary, nullable=True)
    logo_mime      = Column(String(40), nullable=True)
    logo_sha       = Column(String(64), nullable=True)
    status         = Column(String(10), nullable=False, default="brouillon")      # brouillon | pret
    owner_id       = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # cloisonnement
    updated_by     = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at     = Column(DateTime, nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow, nullable=False)
