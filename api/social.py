"""
api/social.py
Routeur social : favoris, annuaire organisateurs + abonnements (followers),
feedback (soumission publique, avis communautaires, modération admin).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from core.permissions import get_current_admin, require_staff_function
from core.database import get_db
from core.limiter import limiter
from core.security import get_current_user, get_optional_user, log_security_event
from models.event import Event
from models.user import User
from models.social import Favorite, Follower, Feedback
from schemas.social import FeedbackCreate, FeedbackUpdate
from utils.serializers import _event_dict

router = APIRouter()


# ── FAVORIS ─────────────────────────────────────────────────────
@router.get("/favorites")
def get_favorites(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    favs = db.query(Favorite).filter(Favorite.user_id == current_user.id).all()
    return [_event_dict(f.event) for f in favs if f.event]


@router.post("/favorites/{event_id}")
def add_favorite(event_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    existing = db.query(Favorite).filter(
        Favorite.user_id == current_user.id,
        Favorite.event_id == event_id,
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        return {"message": "Retiré des favoris", "action": "removed"}
    fav = Favorite(user_id=current_user.id, event_id=event_id)
    db.add(fav)
    db.commit()
    return {"message": "Ajouté aux favoris", "action": "added"}


# ── ORGANISATEURS & ABONNEMENTS ─────────────────────────────────
@router.get("/organizers")
def get_organizers(db: Session = Depends(get_db), current_user: User = Depends(get_optional_user)):
    organizers = db.query(User).filter(User.role == "organizer").all()
    result = []

    for org in organizers:
        events_count = db.query(Event).filter(Event.organizer_id == org.id).count()
        followers_count = db.query(Follower).filter(Follower.organizer_id == org.id).count()

        is_followed = False
        if current_user:
            is_followed = db.query(Follower).filter(
                Follower.user_id == current_user.id,
                Follower.organizer_id == org.id,
            ).first() is not None

        name = org.org_name or org.full_name or "Organisateur Anonyme"
        initials = "".join([w[0] for w in name.split()[:2]]).upper()

        result.append({
            "id": org.id,
            "name": name,
            "type": org.org_type or "Organisation",
            "eventsCount": events_count,
            "followersCount": followers_count,
            "initials": initials,
            "is_followed": is_followed,
        })
    return result


@router.post("/organizers/{org_id}/follow")
def follow_organizer(org_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    org = db.query(User).filter(User.id == org_id, User.role == "organizer").first()
    if not org:
        raise HTTPException(404, "Organisateur introuvable")

    existing = db.query(Follower).filter(
        Follower.user_id == current_user.id,
        Follower.organizer_id == org_id,
    ).first()

    if existing:
        db.delete(existing)
        db.commit()
        return {"message": f"Vous ne suivez plus {org.org_name or org.full_name}", "is_followed": False}
    else:
        new_follow = Follower(user_id=current_user.id, organizer_id=org_id)
        db.add(new_follow)
        db.commit()
        return {"message": f"Vous suivez maintenant {org.org_name or org.full_name}", "is_followed": True}


# ── FEEDBACK PUBLIC ─────────────────────────────────────────────
@router.post("/feedback")
@limiter.limit("5/hour")
def submit_feedback(request: Request, data: FeedbackCreate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_optional_user)):
    if not data.message or len(data.message.strip()) < 5:
        raise HTTPException(400, "Le message doit contenir au moins 5 caractères")
    if len(data.message) > 5000:
        raise HTTPException(400, "Le message est trop long (max 5000 caractères)")
    if data.rating is not None and (data.rating < 1 or data.rating > 5):
        raise HTTPException(400, "La note doit être entre 1 et 5")

    user_agent = request.headers.get("User-Agent", "")[:500]

    new_feedback = Feedback(
        user_id=current_user.id if current_user else None,
        rating=data.rating,
        category=data.category,
        message=data.message.strip(),
        email_contact=data.email_contact.strip() if data.email_contact else None,
        user_agent=user_agent,
        page_origin=data.page_origin,
        status="nouveau",
    )
    db.add(new_feedback)
    db.commit()
    db.refresh(new_feedback)

    log_security_event(
        db, "feedback_submitted", request,
        current_user.id if current_user else None,
        f"Feedback #{new_feedback.id} - rating: {data.rating}",
    )

    return {
        "status": "success",
        "message": "Merci pour votre feedback !",
        "feedback_id": new_feedback.id,
    }


@router.get("/feedback/community")
def get_community_feedbacks(limit: int = 5, db: Session = Depends(get_db)):
    feedbacks = (
        db.query(Feedback)
        .filter(Feedback.rating >= 4, Feedback.status != "ignore")
        .order_by(Feedback.created_at.desc())
        .limit(limit)
        .all()
    )

    items = []
    for f in feedbacks:
        author_name = "Utilisateur"
        author_initials = "??"
        author_role = None

        if f.user_id:
            u = db.query(User).filter(User.id == f.user_id).first()
            if u and u.full_name:
                parts = u.full_name.strip().split()
                if len(parts) >= 2:
                    author_name = f"{parts[0]} {parts[1][0]}."
                    author_initials = f"{parts[0][0]}{parts[1][0]}".upper()
                else:
                    author_name = parts[0]
                    author_initials = parts[0][:2].upper()
                author_role = u.role

        items.append({
            "id": f.id,
            "rating": f.rating,
            "message": f.message,
            "author_name": author_name,
            "author_initials": author_initials,
            "author_role": author_role,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        })
    return items


# ── FEEDBACK ADMIN ──────────────────────────────────────────────
@router.get("/admin/feedbacks")
def list_feedbacks(
    status: Optional[str] = None,
    category: Optional[str] = None,
    rating: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_function("cm")),
):
    query = db.query(Feedback)
    if status:
        query = query.filter(Feedback.status == status)
    if category:
        query = query.filter(Feedback.category == category)
    if rating is not None:
        query = query.filter(Feedback.rating == rating)

    query = query.order_by(Feedback.status == "nouveau", Feedback.created_at.desc())

    total = query.count()
    feedbacks = query.offset(offset).limit(limit).all()

    stats = {
        "total": db.query(Feedback).count(),
        "nouveau": db.query(Feedback).filter(Feedback.status == "nouveau").count(),
        "lu": db.query(Feedback).filter(Feedback.status == "lu").count(),
        "traite": db.query(Feedback).filter(Feedback.status == "traite").count(),
        "ignore": db.query(Feedback).filter(Feedback.status == "ignore").count(),
    }

    rated = db.query(Feedback).filter(Feedback.rating.isnot(None)).all()
    if rated:
        stats["avg_rating"] = round(sum(f.rating for f in rated) / len(rated), 2)
        stats["total_rated"] = len(rated)
    else:
        stats["avg_rating"] = None
        stats["total_rated"] = 0

    # Résolution des auteurs en UNE requête (anti N+1)
    user_ids = {f.user_id for f in feedbacks if f.user_id}
    users_map = {}
    if user_ids:
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        users_map = {u.id: u for u in users}

    items = []
    for f in feedbacks:
        user_info = None
        u = users_map.get(f.user_id)
        if u:
            user_info = {
                "id": u.id,
                "name": u.full_name,
                "phone": u.phone_number,
                "email": u.email,
                "role": u.role,
            }
        items.append({
            "id": f.id,
            "rating": f.rating,
            "category": f.category,
            "message": f.message,
            "email_contact": f.email_contact,
            "user_agent": f.user_agent,
            "page_origin": f.page_origin,
            "status": f.status,
            "admin_note": f.admin_note,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "read_at": f.read_at.isoformat() if f.read_at else None,
            "user": user_info,
        })

    return {"items": items, "total": total, "stats": stats}


@router.patch("/admin/feedbacks/{feedback_id}")
def update_feedback(feedback_id: int, data: FeedbackUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(require_staff_function("cm"))):
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(404, "Feedback introuvable")

    if data.status:
        valid = ["nouveau", "lu", "traite", "ignore"]
        if data.status not in valid:
            raise HTTPException(400, f"Statut invalide. Valeurs: {valid}")
        if fb.status == "nouveau" and data.status != "nouveau" and not fb.read_at:
            fb.read_at = datetime.utcnow()
        fb.status = data.status

    if data.admin_note is not None:
        fb.admin_note = data.admin_note

    db.commit()
    db.refresh(fb)

    return {"status": "success", "feedback_id": fb.id, "new_status": fb.status}

@router.delete("/admin/feedbacks/{feedback_id}")
def delete_feedback(feedback_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_admin)):
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(404, "Feedback introuvable")

    db.delete(fb)
    db.commit()
    return {"status": "success", "message": "Feedback supprimé"}
