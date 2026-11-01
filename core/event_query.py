"""
core/event_query.py — Briques communes des listes d'événements (lot 5) :
fenêtres « aujourd'hui / ce week-end / ce mois-ci » à l'heure de Bangui,
recherche LIKE échappée, statut calculé (liste admin).

event_date est stocké en UTC naïf (comparé partout à datetime.utcnow()).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, not_, or_

from models.event import Event

BANGUI = timezone(timedelta(hours=1))
IN_PROGRESS_HOURS = 6          # pas d'heure de fin : « en cours » = début + 6 h
WATCH_DAYS = 7                 # « à surveiller » : < 10 % de remplissage à J-7
STATUSES = ("avenir", "encours", "complet", "passe", "desactive")


def like(q: str) -> str:
    """Motif ILIKE « contient », jokers de l'utilisateur neutralisés."""
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + q + "%"


def _utc(dt_local: datetime) -> datetime:
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def window(when: str, now: datetime = None):
    """(début, fin) en UTC naïf pour today | weekend | month, sinon None."""
    now = now or datetime.utcnow()
    loc = now.replace(tzinfo=timezone.utc).astimezone(BANGUI)
    midnight = loc.replace(hour=0, minute=0, second=0, microsecond=0)
    if when == "today":
        return _utc(midnight), _utc(midnight + timedelta(days=1))
    if when == "weekend":
        wd = loc.weekday()                       # lundi = 0, samedi = 5
        start = midnight - timedelta(days=wd - 5) if wd >= 5 else midnight + timedelta(days=5 - wd)
        return _utc(start), _utc(start + timedelta(days=7 - start.weekday()))
    if when == "month":
        nxt = (midnight.replace(day=1) + timedelta(days=32)).replace(day=1)
        return _utc(midnight), _utc(nxt)
    return None


def status_filter(status: str, now: datetime):
    """Condition SQL d'un statut calculé (événements publics)."""
    started = now - timedelta(hours=IN_PROGRESS_HOURS)
    active = Event.is_active.is_(True)
    full = Event.seats_sold >= Event.total_seats
    return {
        "desactive": not_(active),
        "passe": and_(active, Event.event_date < started),
        "encours": and_(active, Event.event_date >= started, Event.event_date <= now),
        "complet": and_(active, Event.event_date > now, full),
        "avenir": and_(active, Event.event_date > now, not_(full)),
    }[status]


def watch_filter(now: datetime):
    """À surveiller : à venir ET (complet OU < 10 % de remplissage à J-7)."""
    return and_(Event.is_active.is_(True), Event.event_date > now,
                or_(Event.seats_sold >= Event.total_seats,
                    and_(Event.event_date <= now + timedelta(days=WATCH_DAYS),
                         Event.seats_sold * 10 < Event.total_seats)))


def status_of(e: Event, now: datetime) -> str:
    if not e.is_active:
        return "desactive"
    if e.event_date is None or e.event_date < now - timedelta(hours=IN_PROGRESS_HOURS):
        return "passe"
    if e.event_date <= now:
        return "encours"
    return "complet" if (e.seats_sold or 0) >= (e.total_seats or 0) else "avenir"
