"""
models/__init__.py
Importe TOUS les modèles pour qu'ils soient enregistrés sur Base.metadata.
⚠️ create_all() doit être appelé APRÈS cet import (dans main.py),
sinon certaines tables ne sont jamais créées
(bug historique : partner_requests, password_resets).
"""
from models.user import User, SecurityLog, PasswordReset
from models.event import Event
from models.ticket import Ticket
from models.payment import Payment, PayoutRequest
from models.social import Favorite, Follower, Feedback
from models.partner import PartnerRequest, CancellationRequest
from models.cms import HomepageContent, AdContract, FundraisingCampaign

__all__ = [
    "User", "SecurityLog", "PasswordReset",
    "Event", "Ticket",
    "Payment", "PayoutRequest",
    "Favorite", "Follower", "Feedback",
    "PartnerRequest", "CancellationRequest",
    "HomepageContent", "AdContract", "FundraisingCampaign",
]
