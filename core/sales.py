"""
core/sales.py — Équipe commerciale (lot 4.5) : code commercial, PIN provisoire,
commissions sur les packs Invitations.

Code commercial (users.sales_code)
- Proposé automatiquement à la création d'un commercial : COM-<PRÉNOM> (ASCII,
  majuscules), COM-<PRÉNOM>2… si déjà pris. Même code que le lien
  goevent.africa/invitations?ref=CODE (format [A-Z0-9-]{1,20}).
- Modifiable par l'admin tant qu'aucun devis, événement, pack ou commission ne
  le porte ; définitif ensuite (les liens distribués et les attributions en dépendent).

PIN provisoire : 6 chiffres tirés avec `secrets`, jamais une suite triviale ;
renvoyé une seule fois, jamais stocké en clair ni journalisé.

Commissions (table `commissions`, source_type « invitation_pack ») : une ligne
par pack actif vendu par un commercial, figée à l'activation (base = pack après
remise, HT si TVA ; taux du commercial à cet instant). Statuts : pending (événement
pas encore clôturé) → approved (clôture du mois de l'événement, `period`) → paid
(versée : date + référence) ; cancelled si le pack est annulé avant versement.
"""
import re
import secrets
import unicodedata
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.user import User

TZ = ZoneInfo("Africa/Bangui")
SALES_CODE_RE = re.compile(r"^[A-Z0-9-]{1,20}$")
SALES_FUNCTIONS = ("commercial",)
COMMISSION_SOURCE = "invitation_pack"


# ════════════════════════════════════════════════════════════
# Code commercial
# ════════════════════════════════════════════════════════════
def ascii_upper(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _code_taken(db: Session, code: str, exclude_id: Optional[int] = None) -> bool:
    q = db.query(User.id).filter(func.upper(User.sales_code) == code.upper())
    if exclude_id:
        q = q.filter(User.id != exclude_id)
    return q.first() is not None


def suggest_sales_code(db: Session, full_name: str, exclude_id: Optional[int] = None) -> str:
    first = ascii_upper((full_name or "").split()[0] if (full_name or "").split() else "")[:14] or "VENTE"
    base = "COM-" + first
    for i in range(1, 1000):
        cand = base if i == 1 else "%s%d" % (base, i)
        if not _code_taken(db, cand, exclude_id):
            return cand
    raise HTTPException(409, "Impossible de proposer un code commercial libre : saisissez-le.")


def clean_sales_code(db: Session, raw: Optional[str], exclude_id: Optional[int] = None) -> Optional[str]:
    code = (raw or "").strip().upper()
    if not code:
        return None
    if not SALES_CODE_RE.match(code):
        raise HTTPException(400, "Code commercial : lettres, chiffres et tirets uniquement (20 caractères au plus).")
    if _code_taken(db, code, exclude_id):
        raise HTTPException(409, "Ce code commercial est déjà attribué à un autre compte.")
    return code


def sales_code_locked(db: Session, user_id: int) -> bool:
    """Code définitif dès qu'un devis, un événement privé, un pack ou une commission y est rattaché."""
    from models.commission import Commission
    from models.invitation import InvitationPack, PrivateEventSettings
    from models.invitation_quote import InvitationQuote
    for model, col in ((InvitationQuote, InvitationQuote.owner_id), (PrivateEventSettings, PrivateEventSettings.owner_id),
                       (InvitationPack, InvitationPack.owner_id), (Commission, Commission.beneficiary_user_id)):
        if db.query(col).filter(col == user_id).first() is not None:
            return True
    return False


# ════════════════════════════════════════════════════════════
# PIN provisoire
# ════════════════════════════════════════════════════════════
def is_trivial_pin(pin: str) -> bool:
    if not re.fullmatch(r"\d{6}", pin or ""):
        return True
    if len(set(pin)) <= 2:                         # 000000, 121212, 112211…
        return True
    digits = [int(c) for c in pin]
    steps = {(b - a) % 10 for a, b in zip(digits, digits[1:])}
    if steps in ({1}, {9}):                        # 123456, 987654, 890123…
        return True
    return pin[:3] == pin[3:]                      # 123123


def generate_pin() -> str:
    while True:
        pin = "%06d" % secrets.randbelow(1_000_000)
        if not is_trivial_pin(pin):
            return pin


# ════════════════════════════════════════════════════════════
# Commissions (packs Invitations)
# ════════════════════════════════════════════════════════════
def rate_bp(user: Optional[User]) -> Optional[int]:
    if not user or user.commission_rate is None:
        return None
    return int(round(float(user.commission_rate) * 10000))


def commission_amount(base: int, bp: int) -> int:
    return (int(base) * int(bp) + 5000) // 10000


def commission_base(snapshot: dict) -> int:
    """Prix du pack après remise (options exclues) ; HT si la TVA est activée (même ratio que la commande)."""
    pack = (snapshot or {}).get("pack") or {}
    if (snapshot or {}).get("offered"):
        return 0
    disc = ((snapshot or {}).get("discount") or {}).get("amount") or 0
    base = max(int(pack.get("unit_price") or 0) - int(disc), 0)
    vat = (snapshot or {}).get("vat") or {}
    if vat.get("enabled") and vat.get("prices_include_vat") and vat.get("rate_bp"):
        base = int(round(base * 10000 / (10000 + int(vat["rate_bp"]))))
    return base


def is_seller(user: Optional[User]) -> bool:
    return bool(user and user.role == "staff" and user.staff_function in SALES_FUNCTIONS)


def month_bounds(period: str):
    """'2026-11' → (début, fin) en UTC naïf, bornes du mois à l'heure de Bangui."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period or ""):
        raise HTTPException(400, "Mois attendu au format AAAA-MM.")
    y, m = int(period[:4]), int(period[5:])
    start = datetime(y, m, 1, tzinfo=TZ)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=TZ)
    utc = ZoneInfo("UTC")
    return start.astimezone(utc).replace(tzinfo=None), end.astimezone(utc).replace(tzinfo=None)


def period_of(event_date: Optional[datetime]) -> Optional[str]:
    if not event_date:
        return None
    return event_date.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ).strftime("%Y-%m")


def payout_deadline(period: str) -> str:
    """Versement au plus tard le 5 du mois suivant."""
    y, m = int(period[:4]), int(period[5:])
    return "%04d-%02d-05" % (y + (m == 12), m % 12 + 1)


def today_bangui():
    return datetime.now(TZ).date()


def add_days(d, n: int):
    return d + timedelta(days=n)
