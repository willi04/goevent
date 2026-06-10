"""
core/limiter.py
Instance unique du rate limiter, partagée entre main.py et tous les routeurs.
Une seule instance = un seul compteur cohérent.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
