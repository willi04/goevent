"""
core/quotes.py — Devis numérotés de l'offre Invitations (lot 4.5).

- Numéro DEV-AAAA-NNNN : compteur par année (heure de Bangui) incrémenté par
  INSERT … ON CONFLICT … RETURNING dans la transaction qui insère le devis :
  ni trou ni doublon, même si deux commerciaux valident à la même seconde
  (pas de SEQUENCE : elle laisse des trous).
- Montants : core.invitation_catalog.price() (même calcul que l'activation),
  instantané figé dans le devis.
- Portefeuille : owner_id. Un commercial ne voit que ses devis (404 sinon).
- Conflit (même téléphone) : bloquant tant que le devis d'un autre propriétaire
  est protégé (brouillon / envoyé / accepté, jusqu'à validité + 30 j) ou qu'un
  événement privé à venir d'un autre propriétaire porte ce numéro. Nom ou
  entreprise proche + date d'événement à ± 30 j : simple alerte pour l'admin.
- Données des prospects (devis non convertis) anonymisées 12 mois après la validité.
"""
import re
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from core import sales
from models.invitation import PrivateEventSettings
from models.invitation_quote import InvitationQuote
from models.user import User

VALIDITY_DAYS = 15
PROTECTION_DAYS = 30
SIMILAR_DATE_DAYS = 30
RETENTION_MONTHS = 12
STATUSES = ("brouillon", "envoye", "accepte", "refuse", "converti")
PROTECTED = ("brouillon", "envoye", "accepte")
STATUS_LABELS = {"brouillon": "Brouillon", "envoye": "Envoyé", "accepte": "Accepté", "refuse": "Refusé",
                 "converti": "Converti", "expire": "Expiré"}
STOP_WORDS = {
    "famille", "fam", "mme", "mlle", "mr", "m", "mm", "monsieur", "madame", "mademoiselle", "sarl", "sarlu", "sa",
    "ets", "etablissement", "etablissements", "ste", "societe", "et", "de", "du", "des", "la", "le", "les", "l", "d",
    "chez", "mariage", "dot", "gala", "anniversaire", "bapteme", "conference",
}


# ── normalisation ────────────────────────────────────────────────────
def norm_name(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    words = sorted({w for w in re.split(r"[^a-z0-9]+", s) if len(w) > 1 and w not in STOP_WORDS})
    return " ".join(words)[:160] or None


def similar(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    A, B = set(a.split()), set(b.split())
    inter = len(A & B)
    if not inter:
        return False
    return inter == min(len(A), len(B)) or inter / len(A | B) >= 0.6


def add_months(d: date, n: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


# ── état ─────────────────────────────────────────────────────────────
def effective_status(q: InvitationQuote, today: Optional[date] = None) -> str:
    today = today or sales.today_bangui()
    if q.status in ("brouillon", "envoye") and q.valid_until < today:
        return "expire"
    return q.status


def protected(q: InvitationQuote, today: date) -> bool:
    return q.status in PROTECTED and not q.purged_at and today <= q.valid_until + timedelta(days=PROTECTION_DAYS)


# ── numérotation ─────────────────────────────────────────────────────
def next_number(db: Session, year: int):
    seq = db.execute(text(
        "INSERT INTO invitation_quote_counters (year, last) VALUES (:y, 1) "
        "ON CONFLICT (year) DO UPDATE SET last = invitation_quote_counters.last + 1 RETURNING last"), {"y": year}).scalar()
    return int(seq), "DEV-%d-%04d" % (year, seq)


# ── portefeuille ─────────────────────────────────────────────────────
def get_quote(db: Session, quote_id: int, user: User, lock: bool = False) -> InvitationQuote:
    from core.permissions import sees_all_sales
    qy = db.query(InvitationQuote).filter(InvitationQuote.id == quote_id)
    if lock:
        qy = qy.with_for_update()
    q = qy.first()
    if not q or (not sees_all_sales(user) and q.owner_id != user.id):
        raise HTTPException(404, "Devis introuvable")
    return q


# ── conflits ─────────────────────────────────────────────────────────
def find_conflicts(db: Session, owner_id: int, phone: Optional[str], name_n: Optional[str], company_n: Optional[str],
                   event_date: Optional[date], exclude_id: Optional[int] = None, owner_is_admin: bool = False):
    """→ (bloquant, alerte) : bloquant = devis ou événement d'un AUTRE propriétaire pour ce téléphone ;
    alerte = id d'un devis d'un autre propriétaire au nom / à l'entreprise proche, date à ± 30 j."""
    today = sales.today_bangui()
    blocking = None
    if phone:
        qs = db.query(InvitationQuote).filter(InvitationQuote.phone_norm == phone, InvitationQuote.owner_id != owner_id)
        if exclude_id:
            qs = qs.filter(InvitationQuote.id != exclude_id)
        for other in qs.order_by(InvitationQuote.id).all():
            if protected(other, today):
                blocking = {"kind": "quote", "id": other.id, "number": other.number, "owner_id": other.owner_id}
                break
        if not blocking:
            from models.event import Event
            row = (db.query(PrivateEventSettings, Event)
                   .join(Event, Event.id == PrivateEventSettings.event_id)
                   .filter(PrivateEventSettings.client_phone == phone, Event.is_active == True,  # noqa: E712
                           Event.event_date >= datetime.utcnow() - timedelta(days=1))
                   .all())
            for st, ev in row:
                other_owner = (not owner_is_admin) if st.owner_id is None else st.owner_id != owner_id  # NULL = maison
                if other_owner:
                    blocking = {"kind": "event", "id": ev.id, "title": ev.title, "owner_id": st.owner_id}
                    break
    duplicate = None
    if (name_n or company_n) and event_date:
        lo, hi = event_date - timedelta(days=SIMILAR_DATE_DAYS), event_date + timedelta(days=SIMILAR_DATE_DAYS)
        qs = db.query(InvitationQuote).filter(InvitationQuote.owner_id != owner_id, InvitationQuote.event_date >= lo,
                                              InvitationQuote.event_date <= hi, InvitationQuote.purged_at.is_(None))
        if exclude_id:
            qs = qs.filter(InvitationQuote.id != exclude_id)
        for other in qs.order_by(InvitationQuote.id.desc()).limit(500).all():
            if similar(name_n, other.name_norm) or (company_n and similar(company_n, other.company_norm)):
                duplicate = other.id
                break
    return blocking, duplicate


# ── sérialisation ────────────────────────────────────────────────────
def owners_map(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()}


def quote_out(q: InvitationQuote, viewer_admin: bool, owners: dict, detail: bool = False) -> dict:
    st = effective_status(q)
    o = owners.get(q.owner_id)
    out = {
        "id": q.id, "number": q.number, "status": st, "status_label": STATUS_LABELS.get(st, st),
        "client_name": q.client_name, "client_company": q.client_company, "client_phone": q.client_phone,
        "event_type": q.event_type, "event_date": q.event_date.isoformat() if q.event_date else None,
        "location": q.location, "guests_count": q.guests_count,
        "total_fcfa": q.total_fcfa, "pack_amount_fcfa": q.pack_amount_fcfa,
        "valid_until": q.valid_until.isoformat(), "needs_admin": q.needs_admin and not q.validated_at,
        "owner_id": q.owner_id, "owner_name": (o.full_name if o else None), "sales_code": q.sales_code,
        "event_id": q.event_id, "pack_id": q.pack_id, "purged": bool(q.purged_at),
        "created_at": q.created_at.isoformat(), "sent_at": q.sent_at.isoformat() if q.sent_at else None,
        "accepted_at": q.accepted_at.isoformat() if q.accepted_at else None,
        "refused_reason": q.refused_reason,
        "formula_name": ((q.snapshot or {}).get("formula") or {}).get("name"),
        "pack_size": ((q.snapshot or {}).get("pack") or {}).get("size"),
    }
    if viewer_admin:
        out["possible_duplicate_of"] = q.possible_duplicate_of
        out["conflict_with_quote_id"] = q.conflict_with_quote_id
    if detail:
        out["snapshot"] = q.snapshot
    return out


def fcfa(v) -> str:
    return "{:,}".format(int(v or 0)).replace(",", " ") + " FCFA"


def fr_date(d: Optional[date]) -> str:
    if not d:
        return ""
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
              "novembre", "décembre"]
    return "%s %s %d" % ("1er" if d.day == 1 else d.day, months[d.month - 1], d.year)


def whatsapp_text(q: InvitationQuote) -> str:
    s = q.snapshot or {}
    pack, f = s.get("pack") or {}, s.get("formula") or {}
    lines = ["Devis GoEvent N° %s — Invitations digitales" % q.number]
    head = " · ".join(x for x in [q.event_type, fr_date(q.event_date), q.location] if x)
    if head:
        lines.append(head)
    lines += ["", "%s — %s personnes : %s" % (f.get("name", ""), pack.get("size", ""), fcfa(pack.get("unit_price")))]
    for o in s.get("options") or []:
        lines.append("%s%s : %s" % (o["name"], "" if o["unit"] == "flat" else " × %d" % o["qty"], fcfa(o["total"])))
    if s.get("discount"):
        lines.append("Remise : −%s" % fcfa(s["discount"]["amount"]))
    if s.get("offered"):
        lines.append("Pack offert")
    vat = s.get("vat") or {}
    if vat.get("enabled"):
        lines.append("Total HT : %s · TVA : %s" % (fcfa(vat["ht"]), fcfa(vat["vat"])))
    lines.append(("Total TTC : " if vat.get("enabled") else "Total : ") + fcfa(s.get("amount_due")))
    if s.get("agents_included"):
        lines.append("%d agent(s) GoEvent inclus" % s["agents_included"])
    lines += ["", "Valable jusqu'au %s." % fr_date(q.valid_until),
              "Paiement intégral à la commande, à GoEvent SARLU (espèces contre reçu ou Orange Money)."]
    if q.sales_code:
        lines.append("Réf. : %s" % q.sales_code)
    return "\n".join(lines)


# ── purge des prospects ──────────────────────────────────────────────
def purge_due(db: Session) -> int:
    today = sales.today_bangui()
    rows = (db.query(InvitationQuote).filter(InvitationQuote.purged_at.is_(None), InvitationQuote.purge_after <= today,
                                             InvitationQuote.status != "converti").with_for_update(skip_locked=True).all())
    for q in rows:
        q.client_name, q.client_company, q.client_phone, q.location = "Prospect supprimé", None, None, None
        q.phone_norm = q.name_norm = q.company_norm = None
        q.purged_at = datetime.utcnow()
    if rows:
        db.commit()
    return len(rows)
