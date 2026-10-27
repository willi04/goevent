"""
core/config.py
Configuration centralisée GoEvent.
Validation au DÉMARRAGE via pydantic-settings.
Si une variable critique manque, l'app refuse de booter
au lieu de crasher au premier paiement.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict

# ─── Chantier 17 : Régie publicitaire forfaitaire ─────────────────────

BOOST_PACKAGES = {
    "7d":  {"duration_days": 7,  "amount_fcfa": 10_000,  "label": "7 jours"},
    "30d": {"duration_days": 30, "amount_fcfa": 35_000,  "label": "30 jours"},
    "90d": {"duration_days": 90, "amount_fcfa": 130_000, "label": "90 jours"},
}

FB_PACK_MIN_BUDGET = 35_000   # Budget Facebook minimum (FCFA)
FB_PACK_MARGIN_RATE = 0.30    # 30% de marge GoEvent fixe
# ⚠️ Valeurs PAR DÉFAUT : les tarifs en vigueur se règlent dans le back-office
# (Contenu du site → Tarifs) et sont lus via core/pricing.get_pricing().

# Commission billetterie (CGU art. 6) : modifiable uniquement ici + CGU.
COMMISSION_BUYER_RATE = 0.04      # frais de service à la charge de l'acheteur
COMMISSION_ORGANIZER_RATE = 0.07  # commission à la charge de l'organisateur

# ─── Invitations digitales : moyens de paiement des packs ──────────────
# Activation MANUELLE par l'admin, uniquement après un paiement reçu sur un
# moyen AU NOM DE GoEvent SARLU. La référence (n° de reçu, ID de transaction…)
# est obligatoire et unique par canal. Activer/désactiver un canal :
# variable INVITATION_CHANNELS_ACTIVE (codes séparés par des virgules).
INVITATION_PAYMENT_CHANNELS = [
    {"code": "especes_recu", "label": "Espèces contre reçu GoEvent SARLU", "ref_label": "N° du reçu GoEvent SARLU"},
    {"code": "orange_money_goevent", "label": "Orange Money GoEvent SARLU", "ref_label": "ID de transaction Orange Money"},
    {"code": "virement_ecobank", "label": "Virement Ecobank GoEvent SARLU", "ref_label": "Référence du virement"},
]
# Formules, tailles et prix des packs : catalogue en base (core/invitation_catalog.py, lot 2.2)
INVITATION_RETENTION_DAYS = 30                        # purge des listes d'invités après l'événement
INVITATION_HOUSE_PHONE = "+23600000001"               # compte orga interne (inactif) des événements accompagnés


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── BASE & SÉCURITÉ (obligatoires) ──────────────────────────
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── ENVIRONNEMENT ───────────────────────────────────────────
    ENV: str = "production"

    # ── NGSER / ORANGE MONEY ────────────────────────────────────
    NGSER_BASE_URL: str = "https://securetest.crossroad-africa.net"
    NGSER_AUTH_TOKEN: str               # obligatoire : flux d'achat
    NGSER_PAYMENT_TOKEN: str            # obligatoire : flux d'achat
    NGSER_WITHDRAWAL_TOKEN: str | None = None  # optionnel : retraits orga
    NGSER_SERVICE_NAME: str = "GOEVENT TEST"

    # ── CRON / RÉCONCILIATION ───────────────────────────────────
    RECONCILE_SECRET: str = ""
    
    # ── HASHIDS (slugs événements) ──────────────────────────────
    HASHIDS_SALT: str = "GoEvent-RCA-2026-Bangui-Madoukou-Secret"
    HASHIDS_ALPHABET: str = (
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    )
    HASHIDS_MIN_LENGTH: int = 8

    # ── EMAIL (SMTP Gmail) ──────────────────────────────────────
    SENDER_EMAIL: str = ""
    SENDER_PASSWORD: str = ""

    # ── INVITATIONS ─────────────────────────────────────────────
    # Canaux de paiement actifs (Ecobank : à ajouter à l'ouverture du compte)
    INVITATION_CHANNELS_ACTIVE: str = "especes_recu,orange_money_goevent"
    # Domaine des liens de pass (QR) et de suivi
    PUBLIC_SITE_URL: str = "https://goevent.africa"

    # ── IP CLIENT (rate limit, journaux) ───────────────────────
    # peer | header:<nom> | xff:<n> — cf. core/client_ip.py et docs/07. Défaut = historique.
    CLIENT_IP_STRATEGY: str = "peer"

    @property
    def cors_origins(self) -> list[str]:
        origins = [
            "https://goevent.africa",
            "https://www.goevent.africa",
            "https://goevent-core.vercel.app",
        ]
        if self.ENV == "development":
            origins += [
                "http://localhost:5500",
                "http://localhost:8000",
                "http://127.0.0.1:5500",
            ]
        return origins
        
    @property    
    def NGSER_VERIFY_SSL(self) -> bool:
        # En prod : vérification SSL obligatoire (sécurité des flux d'argent).
        # En dev/test : tolérée désactivée si l'agrégateur a un certif de test invalide.
        return self.ENV == "production"
        
settings = Settings()
