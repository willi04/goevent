"""
utils/serializers.py
Sérialiseurs partagés entre routeurs : transforment les objets SQLAlchemy
en dicts JSON propres et légers pour le frontend.
"""
from utils.slugs import event_id_to_slug


def _event_dict(e, include_organizer=True):
    org = e.organizer_user
    return {
        "id": e.id,
        "title": e.title,
        "description": e.description,
        "slug": event_id_to_slug(e.id),
        "location": e.location,
        "category": e.category,
        "event_date": e.event_date,
        "price": e.price,
        "total_seats": e.total_seats,
        "seats_available": e.total_seats - e.seats_sold,
        "seats_sold": e.seats_sold,
        "is_sold_out": e.seats_sold >= e.total_seats,
        "cover_image_url": e.cover_image_url,
        "is_active": e.is_active,
        "organizer": org.org_name or org.full_name if org else "Organisateur",
        "organizer_role": org.role if org else "artiste",
        "organizer_id": e.organizer_id,
        "ticket_tiers": e.ticket_tiers,
    }
