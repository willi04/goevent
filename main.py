"""
GoEvent — Backend API v2 (architecture modulaire)
Point d'entrée : app, middlewares, montage des routeurs, création des tables.
"""
import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from core.config import settings
from core.database import Base, engine
from core.limiter import limiter
from middleware.security_headers import SecurityHeadersMiddleware

# Import de TOUS les modèles avant create_all
import models  # noqa: F401
from api import ads as ads_router
from api import (
    auth, events, tickets, agent, social,
    organizer, dashboard, admin, payments, promotion,
)
from api import staff as staff_router

Base.metadata.create_all(bind=engine)

ROLES_VALIDES = ("fan", "organizer", "agent")

app = FastAPI(title="GoEvent API", version="2.0.0")

# Sécurité / rate limit
app.add_middleware(SecurityHeadersMiddleware)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["Content-Length"],
    max_age=600,
)

# Fichiers statiques
web_dir = pathlib.Path("web")
if web_dir.exists():
    app.mount("/web", StaticFiles(directory="web"), name="web")

# Routeurs
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(tickets.router)
app.include_router(agent.router)
app.include_router(social.router)
app.include_router(organizer.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(payments.router)
app.include_router(ads_router.router)
app.include_router(staff_router.router)
app.include_router(promotion.router)

@app.get("/")
def root():
    return {
        "app": "GoEvent API",
        "version": "2.0.0",
        "status": "En ligne",
        "roles": list(ROLES_VALIDES),
        "docs": "/docs",
    }
