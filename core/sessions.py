"""
core/sessions.py — Séances d'un événement privé sur plusieurs jours (ou plusieurs soirées).

Règle : avec des séances, un pass donne ses places à CHAQUE séance (FONEJ : J1 et J2,
7h–16h) ; sans séance, il les donne une seule fois pour tout l'événement (comportement
historique, inchangé). Une séance = [début − ouverture des portes, fin], heures UTC en base,
affichage à l'heure de Bangui. Une soirée qui passe minuit reste UNE séance (20h → 2h).

Un retour dans la même séance (sortie puis retour) n'est jamais refusé d'office : l'agent
décide ; s'il accepte, l'entrée est journalisée avec reentry = true et n'est comptée ni dans
les entrées ni dans le bilan des présents.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.invitation import EventSession, GuestCheckin

MAX_SESSIONS = 14
MAX_SESSION_HOURS = 24
MAX_DOORS_MIN = 240

_JOURS_COURTS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_MOIS_COURTS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def list_sessions(db: Session, event_id: int) -> List[EventSession]:
    return (db.query(EventSession).filter(EventSession.event_id == event_id)
            .order_by(EventSession.starts_at, EventSession.id).all())


def window_start(s: EventSession) -> datetime:
    return s.starts_at - timedelta(minutes=s.doors_before_min or 0)


def find_session(sessions: List[EventSession], at: datetime) -> Optional[EventSession]:
    """Séance dont la fenêtre de contrôle (portes ouvertes → fin) contient `at`."""
    for s in sessions:
        if window_start(s) <= at <= s.ends_at:
            return s
    return None


def next_session(sessions: List[EventSession], at: datetime) -> Optional[EventSession]:
    return next((s for s in sessions if window_start(s) > at), None)


def reference_session(sessions: List[EventSession], at: datetime) -> Optional[EventSession]:
    """Séance affichée dans les chiffres : en cours, sinon la dernière commencée, sinon la première."""
    if not sessions:
        return None
    cur = find_session(sessions, at)
    if cur:
        return cur
    started = [s for s in sessions if window_start(s) <= at]
    return started[-1] if started else sessions[0]


def last_end(sessions: List[EventSession]) -> Optional[datetime]:
    return max((s.ends_at for s in sessions), default=None)


def _local(dt):
    from core.invitations import local
    return local(dt)


def _hm(d) -> str:
    return "%dh%02d" % (d.hour, d.minute)


def session_label(s: EventSession) -> str:
    """« Jour 1 · ven. 20 nov. · 7h00–16h00 »."""
    a, b = _local(s.starts_at), _local(s.ends_at)
    return "%s · %s %d %s · %s–%s" % (s.label, _JOURS_COURTS[a.weekday()], a.day, _MOIS_COURTS[a.month - 1], _hm(a), _hm(b))


def when_label(s: EventSession) -> str:
    """« Jour 2, sam. 21 nov. à 7h00 (portes à 6h00) »."""
    a, w = _local(s.starts_at), _local(window_start(s))
    out = "%s, %s %d %s à %s" % (s.label, _JOURS_COURTS[a.weekday()], a.day, _MOIS_COURTS[a.month - 1], _hm(a))
    return out + (" (portes à %s)" % _hm(w) if w != a else "")


def dates_label(sessions: List[EventSession]) -> str:
    """« 20 & 21 nov. 2026 · 7h00–16h00 », « 20, 21 & 23 nov. 2026 », « 30 nov. & 1er déc. 2026 »."""
    if not sessions:
        return ""
    days, seen = [], set()
    for s in sessions:
        d = _local(s.starts_at)
        if d.date() not in seen:
            seen.add(d.date())
            days.append(d)
    parts, i = [], 0
    while i < len(days):            # regroupe les jours d'un même mois
        j = i
        while j + 1 < len(days) and (days[j + 1].month, days[j + 1].year) == (days[i].month, days[i].year):
            j += 1
        nums = ["1er" if d.day == 1 else str(d.day) for d in days[i:j + 1]]
        txt = nums[0] if len(nums) == 1 else ", ".join(nums[:-1]) + " & " + nums[-1]
        parts.append("%s %s" % (txt, _MOIS_COURTS[days[i].month - 1]))
        i = j + 1
    label = (" & ".join(parts) if len(parts) <= 2 else ", ".join(parts)) + " %d" % days[-1].year
    hours = {(_hm(_local(s.starts_at)), _hm(_local(s.ends_at))) for s in sessions}
    if len(hours) == 1:
        a, b = hours.pop()
        label += " · %s–%s" % (a, b)
    return label


def session_out(s: EventSession) -> dict:
    iso = lambda d: d.isoformat() + "Z"  # noqa: E731
    return {"id": s.id, "label": s.label, "starts_at": iso(s.starts_at), "ends_at": iso(s.ends_at),
            "doors_before_min": s.doors_before_min, "display": session_label(s)}


def used_in_session(db: Session, guest_id: int, session_id: int) -> int:
    """Personnes entrées avec ce pass pendant la séance (retours et conflits exclus)."""
    return int(db.query(func.coalesce(func.sum(GuestCheckin.count), 0)).filter(
        GuestCheckin.guest_id == guest_id, GuestCheckin.session_id == session_id,
        GuestCheckin.reentry == False, GuestCheckin.conflict == False).scalar() or 0)  # noqa: E712


def last_entry_at(db: Session, guest_id: int, session_id: int) -> Optional[datetime]:
    return db.query(func.max(GuestCheckin.scanned_at)).filter(
        GuestCheckin.guest_id == guest_id, GuestCheckin.session_id == session_id,
        GuestCheckin.conflict == False).scalar()  # noqa: E712


def usage_by_session(db: Session, event_id: int) -> dict:
    """{guest_id: {session_id: personnes entrées}} (retours et conflits exclus)."""
    out = {}
    for gid, sid, n in (db.query(GuestCheckin.guest_id, GuestCheckin.session_id, func.sum(GuestCheckin.count))
                        .filter(GuestCheckin.event_id == event_id, GuestCheckin.session_id.isnot(None),
                                GuestCheckin.reentry == False, GuestCheckin.conflict == False)  # noqa: E712
                        .group_by(GuestCheckin.guest_id, GuestCheckin.session_id).all()):
        out.setdefault(gid, {})[sid] = int(n or 0)
    return out


def require_current(sessions: List[EventSession], now: datetime) -> EventSession:
    """Séance en cours, sinon 400 avec la prochaine séance (ou la fin de l'événement)."""
    s = find_session(sessions, now)
    if s:
        return s
    nxt = next_session(sessions, now)
    if nxt:
        raise HTTPException(400, "Pas de séance en cours. Prochaine : %s." % when_label(nxt))
    raise HTTPException(400, "Événement terminé : plus aucune séance de contrôle.")


def validate(items: list) -> list:
    """Contrôle d'une liste de séances saisie au back-office. Retourne les séances triées.
    items : dicts {id?, label, starts_at, ends_at, doors_before_min} (datetimes UTC naïfs)."""
    if len(items) > MAX_SESSIONS:
        raise HTTPException(400, "%d séances au maximum." % MAX_SESSIONS)
    out = sorted(items, key=lambda x: x["starts_at"])
    for i, it in enumerate(out):
        label = (it.get("label") or "").strip()
        if not label:
            raise HTTPException(400, "Chaque séance doit avoir un nom (ex. « Jour 1 »).")
        it["label"] = label[:60]
        if it["ends_at"] <= it["starts_at"]:
            raise HTTPException(400, "« %s » : la fin doit être après le début." % label)
        if it["ends_at"] - it["starts_at"] > timedelta(hours=MAX_SESSION_HOURS):
            raise HTTPException(400, "« %s » : %d h maximum par séance." % (label, MAX_SESSION_HOURS))
        doors = it.get("doors_before_min")
        it["doors_before_min"] = 60 if doors is None else int(doors)
        if not 0 <= it["doors_before_min"] <= MAX_DOORS_MIN:
            raise HTTPException(400, "Ouverture des portes : entre 0 et %d minutes avant." % MAX_DOORS_MIN)
        if i and out[i - 1]["ends_at"] > it["starts_at"] - timedelta(minutes=it["doors_before_min"]):
            raise HTTPException(400, "« %s » commence (portes comprises) avant la fin de « %s »."
                                % (label, out[i - 1]["label"]))
    return out
