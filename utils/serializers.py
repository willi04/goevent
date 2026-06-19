"""
utils/serializers.py
Sérialiseurs partagés entre routeurs : transforment les objets SQLAlchemy
en dicts JSON propres et légers pour le frontend.
"""
from utils.slugs import event_id_to_slug


def _event_dict(e, include_organizer=True):
    org = e.organizer_user
    
    # Résolution organizer-safe (ne crashe pas si org_user est None)
    if org:
        organizer_name = org.org_name or org.full_name or "Organisateur"
        organizer_role = org.role or "artiste"
    else:
        organizer_name = "Organisateur supprimé"
        organizer_role = "artiste"
    
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
        "organizer": organizer_name,
        "organizer_role": organizer_role,
        "organizer_id": e.organizer_id,
        "ticket_tiers": e.ticket_tiers,
    }
