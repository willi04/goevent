"""
core/ngser.py
Service centralisé pour l'agrégateur de paiement NGSER (Orange Money).
Tous les appels à l'API NGSER passent par ici : sessions d'achat (B2B entrant)
et sessions de retrait (B2C sortant vers l'organisateur).

⚠️ ZONE FINANCIÈRE — toute modification ici impacte des flux d'argent réels.
"""
import httpx

from core.config import settings


async def create_payment_session(order_id: str, amount: int) -> tuple[int, dict]:
    """
    Crée une session de paiement (achat de billet par un fan).
    Retourne (status_code_http, reponse_json).
    Lève httpx.* en cas d'échec réseau (à gérer par l'appelant).
    """
    payload = {
        "name": settings.NGSER_SERVICE_NAME,
        "authentication_token": settings.NGSER_AUTH_TOKEN,
        "currency": "XAF",
        "operation_token": settings.NGSER_PAYMENT_TOKEN,
        "order": order_id,
        "transaction_amount": amount,
    }

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{settings.NGSER_BASE_URL}/v3/sessions",
            json=payload,
            timeout=15.0,
        )

    try:
        data = response.json()
    except Exception:
        data = {}

    return response.status_code, data


async def create_withdrawal_session(order_id: str, amount: int, customer_phone: str) -> tuple[int, dict, str]:
    """
    Crée une session de retrait (versement B2C vers l'organisateur).
    Retourne (status_code_http, reponse_json, texte_brut).
    Le texte brut est renvoyé pour le logging (NGSER renvoie parfois du non-JSON).
    """
    payload = {
        "name": settings.NGSER_SERVICE_NAME,
        "authentication_token": settings.NGSER_AUTH_TOKEN,
        "operation_token": settings.NGSER_WITHDRAWAL_TOKEN,
        "currency": "XAF",
        "order": order_id,
        "transaction_amount": amount,
        "customer": customer_phone,
    }

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{settings.NGSER_BASE_URL}/v3/sessions",
            json=payload,
            timeout=15.0,
        )

    raw_text = response.text
    try:
        data = response.json()
    except Exception:
        data = {}

    return response.status_code, data, raw_text
