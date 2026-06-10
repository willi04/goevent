"""
api/admin.py
Routeur administration transverse : stats globales, gestion users/events/partners,
validation des retraits (appel NGSER B2C), demandes d'annulation, logs de sécurité.
Toutes les routes exigent role == "admin".
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user, log_security_event
from core.ngser import create_withdrawal_session
from models.event import Event
from models.ticket import Ticket
from models.payment import Payment, PayoutRequest
from models.partner import PartnerRequest, CancellationRequest
from models.user import User, SecurityLog
from utils.serializers import _event_dict

router = APIRouter()
