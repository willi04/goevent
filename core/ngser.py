"""
core/ngser.py
Service centralisé pour l'agrégateur de paiement NGSER (Orange Money).
Tous les appels à l'API NGSER passent par ici : sessions d'achat (B2B entrant)
et sessions de retrait (B2C sortant vers l'organisateur).

⚠️ ZONE FINANCIÈRE — toute modification ici impacte des flux d'argent réels.
La vérification SSL (verify) est pilotée par l'environnement : forcée en prod.
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

    async with httpx.AsyncClient(verify=settings.NGSER_VERIFY_SSL) as client:
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

    async with httpx.AsyncClient(verify=settings.NGSER_VERIFY_SSL) as client:
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

async def authenticate(order_id: str) -> str | None:
    """
    Authentification préalable au check de statut (doc NGSER p.13, section 5.1).
    Génère un token de sécurité (valide 30 min) à mettre dans le header
    Authorization du check_payment_status.
    Retourne le auth_token, ou None si échec.
    """
    payload = {
        "auth": {
            "name": settings.NGSER_SERVICE_NAME,
            "authentication_token": settings.NGSER_AUTH_TOKEN,
            "order": order_id,
        }
    }

    async with httpx.AsyncClient(verify=settings.NGSER_VERIFY_SSL) as client:
        response = await client.post(
            f"{settings.NGSER_BASE_URL}/service/auth",
            json=payload,
            timeout=15.0,
        )

    try:
        data = response.json()
    except Exception:
        return None

    return data.get("auth_token")


async def check_payment_status(order_id: str) -> tuple[int, dict]:
    """
    Vérifie l'état d'un paiement auprès de NGSER (doc NGSER p.14, section 5.2).
    Nécessite un auth_token obtenu via authenticate().
    Retourne (status_code_http, reponse_json).

    Codes NGSER (doc p.16) :
      1 = SUCCESS, 0 = FAIL (rejeté), 2 = FAIL (solde insuffisant),
      3 = PENDING, 4 = NOT EXIST (HTTP 404), 5 = FAIL (timeout), 6 = FAIL (inconnu).
    """
    auth_token = await authenticate(order_id)
    if not auth_token:
        return 0, {"status": "FAIL", "errors_messages": ["Échec authentification NGSER"]}

    async with httpx.AsyncClient(verify=settings.NGSER_VERIFY_SSL) as client:
        response = await client.post(
            f"{settings.NGSER_BASE_URL}/check_payment_status/{order_id}",
            headers={"Authorization": f"Token {auth_token}"},
            timeout=15.0,
        )

    try:
        data = response.json()
    except Exception:
        data = {}

    return response.status_code, data
