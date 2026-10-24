"""
core/zones.py — Emplacements (zones) des pages publiques.

Une zone = un conteneur placé dans le HTML (<div data-zone="clé">) où le
back-office ajoute des blocs (table zone_blocks). Les zones sont FIXES : le
design reste maîtrisé sur mobile, et chaque zone publicitaire est un format
vendable (cf. core/pricing.py → ad_formats).

mode    : "rotate" → un bloc tiré au hasard à chaque affichage (pubs)
          "stack"  → tous les blocs, dans l'ordre (cartes, annonces)
          "infeed" → blocs insérés dans une grille toutes les `every` cartes
empty   : comportement si aucun bloc en diffusion (réglable dans l'admin)
          "hide"   → la zone disparaît
          "house"  → encart standard « Emplacement disponible » → page Partenaires
          "static" → le contenu HTML d'origine reste affiché (non réglable)
"""

KINDS = {
    "image": "Affiche / image",
    "banner": "Bannière partenaire",
    "announcement": "Annonce",
    "cards": "Groupe de cartes",
}

THEMES = ("green", "dark", "light", "gold")

# Icônes proposées pour les cartes (Material Symbols)
CARD_ICONS = ("search", "payments", "qr_code_2", "confirmation_number", "event", "celebration",
              "music_note", "theater_comedy", "sports_soccer", "groups", "verified", "bolt",
              "favorite", "star", "campaign", "handshake", "smartphone", "lock", "support_agent", "info")

ZONES = [
    # clé, page, libellé, description, mode, types autorisés, vide par défaut, format tarif
    {"key": "home.after_featured", "page": "index", "label": "Accueil · entre « En vedette » et « À venir »",
     "description": "Bandeau pleine largeur entre les deux listes d'événements.",
     "mode": "rotate", "kinds": ["image", "banner", "announcement"], "empty": "house", "format": "home_banner"},
    {"key": "home.how", "page": "index", "label": "Accueil · Comment ça marche",
     "description": "Remplace la section « Comment ça marche » : cartes ajoutables/supprimables.",
     "mode": "stack", "kinds": ["cards", "announcement"], "empty": "static", "format": None},
    {"key": "home.organizer", "page": "index", "label": "Accueil · Bloc organisateur",
     "description": "Remplace le bloc vert « Vous êtes artiste ou organisateur ? ».",
     "mode": "stack", "kinds": ["banner", "image", "announcement"], "empty": "static", "format": None},
    {"key": "home.before_footer", "page": "index", "label": "Accueil · avant le pied de page",
     "description": "Dernier bandeau de la page d'accueil.",
     "mode": "rotate", "kinds": ["image", "banner", "announcement"], "empty": "hide", "format": "home_banner"},
    {"key": "events.sidebar", "page": "event", "label": "Événements · colonne sous les filtres",
     "description": "Ordinateur uniquement, reste visible au défilement. Sur mobile : voir la carte sponsorisée.",
     "mode": "rotate", "kinds": ["image", "banner", "announcement"], "empty": "house", "format": "events_sidebar"},
    {"key": "events.infeed", "page": "event", "label": "Événements · carte sponsorisée dans la liste",
     "description": "Carte insérée dans la grille d'événements (mobile et ordinateur).",
     "mode": "infeed", "kinds": ["image", "banner"], "empty": "hide", "format": "events_infeed", "every": 6},
    {"key": "event_detail.under_tickets", "page": "event_detail", "label": "Fiche événement · sous la billetterie",
     "description": "Sous le bloc d'achat de billets.",
     "mode": "rotate", "kinds": ["image", "banner", "announcement"], "empty": "hide", "format": "event_detail"},
    {"key": "fan.top", "page": "fan", "label": "Espace fan · haut du tableau de bord",
     "description": "Sous le message d'accueil du fan connecté.",
     "mode": "rotate", "kinds": ["image", "banner", "announcement"], "empty": "hide", "format": "fan_space"},
    {"key": "fan.before_recommended", "page": "fan", "label": "Espace fan · avant « Événements recommandés »",
     "description": "Bandeau au-dessus des recommandations.",
     "mode": "rotate", "kinds": ["image", "banner", "announcement"], "empty": "hide", "format": "fan_space"},
]

ZONE_PAGES = {"index": "Accueil", "event": "Événements", "event_detail": "Fiche événement", "fan": "Espace fan"}
ZONE_BY_KEY = {z["key"]: z for z in ZONES}


def zone_settings(stored: dict, zone: dict) -> dict:
    """Réglages effectifs d'une zone (stockés ∪ défauts)."""
    s = (stored or {}).get(zone["key"], {}) or {}
    empty = zone["empty"]
    if empty != "static" and s.get("empty") in ("hide", "house"):
        empty = s["empty"]
    every = zone.get("every")
    if every and isinstance(s.get("every"), int) and 3 <= s["every"] <= 24:
        every = s["every"]
    return {"empty": empty, "every": every}
