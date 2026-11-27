"""scripts/seed_inv_cadres.py — Seed des 20 cadres d'invitation (Lot 6).

Enregistre les CADRES (layouts) des 2 bibliothèques : familial (B1-B10) et
corporate (C1-C10). Ne crée PAS les skins (table inv_modeles) : un cadre est
une structure, la peau (palette/polices) vient ensuite.

Idempotent : upsert par `code` (slug immuable). Rejouable 100 fois.
Met à jour name/scenes/champs/suggest_for/sort_order si le cadre existe déjà,
sans jamais toucher à son id (les designs qui le référencent restent valides).

    python scripts/seed_inv_cadres.py            # applique
    python scripts/seed_inv_cadres.py --dry-run  # affiche sans écrire
"""
import sys

from core.database import SessionLocal
from models.invitation import InvModeleCadre

# ── Schémas de fiche typés (rendus par l'écran admin Design, cf. plan §3) ──
# "list[...]" = tableau d'objets aux clés listées.
CHAMPS_FAMILIAL = {
    "display_names": "text",          # « Édiane & Friedrich »
    "eyebrow":       "text",          # « Nous nous marions »
    "verset":        "textarea",
    "reference":     "text",
    "intro":         "textarea",
    "body":          "textarea",
    "closing":       "text",
    "date_label":    "text",
    "lieu":          "text",
    "programme":     "list[heure,label,desc,icone]",
}
CHAMPS_CORPORATE = {
    "titre":        "text",
    "sous_titre":   "text",
    "organisateur": "text",
    "logo_url":     "image",
    "intervenants": "list[nom,role,societe,photo]",
    "sessions":     "list[heure,titre,intervenant]",
    "lieu":         "text",
    "sponsors":     "list[nom,logo]",
}

# Cadres au contenu propre (lot 6, nov. 2026) : c2 Accréditation, c5 Cérémonie officielle
CHAMPS_BY_CODE = {
    "c2": {"organisateur": "text", "titre": "text", "sous_titre": "text", "logo_url": "image", "lieu": "text",
           "consignes": "textarea"},
    "c5": {"organisateur": "text", "formule": "text", "titre": "text", "sous_titre": "text", "logo_url": "image",
           "lieu": "text", "sessions": "list[heure,titre,intervenant]"},
}

# scenes : enveloppe (animation d'ouverture) + page + programme + credential.
# pass = "pass" (grand public) | "badge" (corporate, société/rôle).
FAMILIAL_SCENES   = {"enveloppe": True,  "page": True, "programme": True,  "pass": "pass"}
CORP_SCENES       = {"enveloppe": False, "page": True, "programme": False, "pass": "badge"}
CORP_SCENES_AGEN  = {"enveloppe": False, "page": True, "programme": True,  "pass": "badge"}
# Ouverture animée côté corporate : badge qui descend (c1 c2 c3 c7 c9) ou enveloppe officielle
# (c4 c5, et les invités officiels de tous ces cadres). c6 c8 c10 : aucune (efficacité).
CORP_SCENES_OPEN  = {"enveloppe": True,  "page": True, "programme": False, "pass": "badge"}

FAM_TYPES = ["mariage", "dot", "fiancailles", "bapteme", "anniversaire"]

# ── Bibliothèque familiale (B1-B10) ──
FAMILIAL = [
    ("b1",  "Papier-décoré",        "Photo haut + papier décoré ethnique"),
    ("b2",  "Éditorial-moderne",    "Photo encadrée + grand serif"),
    ("b3",  "Cadre-ornemental",     "Arche + cadre doré"),
    ("b4",  "Pleine photo / affiche","Photo plein écran, prénoms en surimpression"),
    ("b5",  "Split asymétrique",    "Photo pleine hauteur + bloc texte latéral"),
    ("b6",  "Médaillon couronne",   "Photo ronde + couronne florale"),
    ("b7",  "Polaroid / album",     "Polaroid incliné, fond kraft"),
    ("b8",  "Minimal typographique","Typo géante + vignette ronde"),
    ("b9",  "Magazine / bandeau",   "Masthead + photo pleine largeur"),
    ("b10", "Art déco géométrique", "Hexagone doré, lignes déco, noir + or"),
]

# ── Bibliothèque corporate (C1-C10) : (code, nom, structure, scenes, types) ──
CORPORATE = [
    ("c1",  "Keynote",              "Titre fort + date/lieu + intervenants + teaser agenda", CORP_SCENES_OPEN, ["conference", "sommet"]),
    ("c2",  "Accréditation",        "Carte nominative colorée selon la catégorie (presse, staff…)", CORP_SCENES_OPEN, ["accreditation", "conference", "salon"]),
    ("c3",  "Affiche-pro / Lancement","Affiche produit, titre bold, CTA",                     CORP_SCENES_OPEN, ["lancement", "produit"]),
    ("c4",  "Gala corporate",       "Élégant, logo, sobre",                                   CORP_SCENES_OPEN, ["gala", "soiree"]),
    ("c5",  "Cérémonie officielle", "Formule protocolaire, filets, déroulé numéroté",          CORP_SCENES_OPEN, ["ceremonie", "institutionnel", "ag"]),
    ("c6",  "Agenda / Programme",   "La timeline comme héros",                                CORP_SCENES_AGEN, ["seminaire", "journee_pro"]),
    ("c7",  "Speakers grid",        "Grille de portraits d'intervenants",                     CORP_SCENES_OPEN, ["conference", "sommet"]),
    ("c8",  "Minimal corporate",    "Marque + titre, très aéré",                              CORP_SCENES,      ["ag", "institutionnel"]),
    ("c9",  "Expo / Salon",         "Stand, numéro de stand, plan",                           CORP_SCENES_OPEN, ["salon", "expo"]),
    ("c10", "Formation / Atelier",  "Workshop, places limitées, matériel",                    CORP_SCENES_AGEN, ["formation", "atelier"]),
]


def _rows():
    rows = []
    for i, (code, name, struct) in enumerate(FAMILIAL, start=1):
        rows.append(dict(code=code, famille="familial", name=name, scenes=FAMILIAL_SCENES,
                         champs=CHAMPS_FAMILIAL, suggest_for=FAM_TYPES, sort_order=i,
                         _struct=struct))
    for i, (code, name, struct, scenes, types) in enumerate(CORPORATE, start=1):
        rows.append(dict(code=code, famille="corporate", name=name, scenes=scenes,
                         champs=CHAMPS_BY_CODE.get(code, CHAMPS_CORPORATE), suggest_for=types, sort_order=i,
                         _struct=struct))
    return rows


def main(dry_run=False):
    db = SessionLocal()
    created = updated = 0
    try:
        existing = {c.code: c for c in db.query(InvModeleCadre).all()}
        for r in _rows():
            struct = r.pop("_struct")  # commentaire humain, pas stocké
            cur = existing.get(r["code"])
            if cur is None:
                print(f"  + {r['code']:5} {r['famille']:9} {r['name']}  — {struct}")
                if not dry_run:
                    db.add(InvModeleCadre(**r))
                created += 1
            else:
                changed = (cur.name != r["name"] or cur.scenes != r["scenes"]
                           or cur.champs != r["champs"] or cur.suggest_for != r["suggest_for"]
                           or cur.sort_order != r["sort_order"] or cur.famille != r["famille"])
                if changed:
                    print(f"  ~ {r['code']:5} maj")
                    if not dry_run:
                        cur.name = r["name"]; cur.famille = r["famille"]
                        cur.scenes = r["scenes"]; cur.champs = r["champs"]
                        cur.suggest_for = r["suggest_for"]; cur.sort_order = r["sort_order"]
                    updated += 1
        if not dry_run:
            db.commit()
        print(f"\n{'[dry-run] ' if dry_run else ''}cadres : {created} créés, {updated} mis à jour, "
              f"{len(_rows())} au total.")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
