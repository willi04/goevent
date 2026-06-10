"""
core/config.py
Configuration centralisée GoEvent.
Validation au DÉMARRAGE via pydantic-settings.
Si une variable critique manque, l'app refuse de booter
au lieu de crasher au premier paiement.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ── HASHIDS (slugs événements) ──────────────────────────────
    HASHIDS_SALT: str = "GoEvent-RCA-2026-Bangui-Madoukou-Secret"
    HASHIDS_ALPHABET: str = (
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    )
    HASHIDS_MIN_LENGTH: int = 8

    # ── EMAIL (SMTP Gmail) ──────────────────────────────────────
    SENDER_EMAIL: str = ""
    SENDER_PASSWORD: str = ""

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
