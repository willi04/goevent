"""
core/database.py
Engine PostgreSQL (Neon), session factory, Base déclarative, dépendance get_db.
Config Neon-friendly : pool_pre_ping réveille les connexions suspendues
(scale-to-zero de Neon serverless).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,        # réveille les connexions Neon suspendues
    pool_recycle=300,          # recycle toutes les 5 min
    pool_size=5,
    max_overflow=10,
    connect_args={
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
