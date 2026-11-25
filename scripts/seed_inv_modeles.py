"""scripts/seed_inv_modeles.py — Seed des gabarits (skins) d'invitation (Lot 6, étape 2).

Un gabarit (inv_modeles) = un CADRE × un SKIN (palette + polices + ornements).
Étape 2 : 1 skin abouti par cadre (20 gabarits), repris des 20 démos validées.
La variété de couleurs supplémentaire viendra du moteur de recolorisation
(palette extraite de l'affiche du client), pas de skins figés en base.

Idempotent : un gabarit n'est (ré)inséré que si son `name` n'existe pas déjà.
Le FK cadre_id est résolu par le `code` du cadre (seedé à l'étape 1).

    python scripts/seed_inv_modeles.py            # applique (via DATABASE_URL)
    python scripts/seed_inv_modeles.py --dry-run  # affiche sans écrire
    python scripts/seed_inv_modeles.py --print-sql # imprime le SQL idempotent (sans DB)
"""
import json
import sys

# (cadre_code, nom_gabarit, mode, couleurs, police_titre, ornements)
# couleurs : fond, accent, clair (cartes), texte (+ accent2/highlight optionnels).
SKINS = [
    # ── Familial (enveloppe = True) ──
    ("b1",  "Papier-décoré — Terracotta",      "clair",  {"fond": "#f2ece1", "accent": "#b5603a", "accentD": "#97451f", "clair": "#fcf8f0", "texte": "#33291f", "muted": "#9a8a74"}, "Fraunces",            {"motif": "papier-ethnique"}),
    ("b2",  "Éditorial-moderne — Terre",        "clair",  {"fond": "#efe9dd", "accent": "#c0603a", "accentD": "#9a471f", "clair": "#fbf8f1", "texte": "#2a2620", "muted": "#938a78"}, "Fraunces",            {"regle": "fine"}),
    ("b3",  "Cadre-ornemental — Royal doré",    "sombre", {"fond": "#1b1e25", "accent": "#d8b878", "accentD": "#b89a5a", "clair": "#20242b", "texte": "#f0e8d6", "muted": "#9a9488"}, "Cormorant Garamond",  {"cadre": "arche-or"}),
    ("b4",  "Pleine photo — Nuit argent",       "sombre", {"fond": "#16233f", "accent": "#b9c4d8", "accentD": "#8a98b4", "clair": "#182542", "texte": "#eaf0fa", "muted": "#93a0bb"}, "Playfair Display",    {"overlay": "degrade-bas"}),
    ("b5",  "Split asymétrique — Rose poudré",  "clair",  {"fond": "#f7efe9", "accent": "#b56b67", "accentD": "#9a5450", "clair": "#fdf9f6", "texte": "#4a3a38", "muted": "#9c8b85"}, "Fraunces",            {"sage": "#8a9a7b"}),
    ("b6",  "Médaillon couronne — Sauge",       "clair",  {"fond": "#f4efe3", "accent": "#bf6a42", "accentD": "#9a4f2c", "clair": "#fcf8ef", "texte": "#3a2e24", "muted": "#9a8c76"}, "Cormorant Garamond",  {"couronne": "florale", "sage": "#7e9068"}),
    ("b7",  "Polaroid / album — Kraft",         "clair",  {"fond": "#d8c6a2", "accent": "#bf6436", "accentD": "#9a4a22", "clair": "#fbf7ee", "texte": "#4a3a2a", "muted": "#8a7a60"}, "Cormorant Garamond",  {"fond": "kraft", "polaroid": True}),
    ("b8",  "Minimal typographique — Terre",    "clair",  {"fond": "#f4f1e8", "accent": "#b5603a", "accentD": "#97451f", "clair": "#fbf9f3", "texte": "#1c1b19", "muted": "#9a9488"}, "Fraunces",            {"style": "typo-geante"}),
    ("b9",  "Magazine / bandeau — Festif",      "clair",  {"fond": "#fbf6ef", "accent": "#e4559a", "accentD": "#c93b80", "highlight": "#f2a93c", "clair": "#fffdf9", "texte": "#241f2b", "muted": "#8d8597"}, "Fraunces",     {"masthead": True, "teal": "#2bb3a3"}),
    ("b10", "Art déco — Noir & or",             "sombre", {"fond": "#141310", "accent": "#c9a24b", "accentD": "#a8863a", "clair": "#1a1813", "texte": "#f3ead3", "muted": "#9a8f72"}, "Marcellus",           {"style": "art-deco", "hexagone": True}),
    # ── Corporate (enveloppe = False) ──
    ("c1",  "Keynote — Nuit azur",              "sombre", {"fond": "#13151b", "accent": "#4da3ff", "accentD": "#2f78c7", "clair": "#191c24", "texte": "#eaf0f8", "muted": "#8a93a3", "vip": "#f0b030"}, "Plus Jakarta Sans", {}),
    ("c2",  "Badge-first — Indigo",             "clair",  {"fond": "#eef0f6", "accent": "#4f46e5", "accentD": "#3a32c0", "clair": "#ffffff", "texte": "#1a1b2e", "muted": "#767a95", "vip": "#e0a82e"}, "Plus Jakarta Sans", {}),
    ("c3",  "Lancement — Néon",                 "sombre", {"fond": "#140a2a", "accent": "#6d28d9", "accent2": "#db2777", "highlight": "#ffd84d", "clair": "#1b1230", "texte": "#f3ecff", "muted": "#b9a8d6"}, "Plus Jakarta Sans", {"degrade": True}),
    ("c4",  "Gala — Or sombre",                 "sombre", {"fond": "#0e1420", "accent": "#c9a860", "accentD": "#ecd9a8", "clair": "#141b29", "texte": "#eef2f6", "muted": "#8a94a6"}, "Cormorant Garamond",  {"sobre": True}),
    ("c5",  "Webinaire / Live — Cyan",          "sombre", {"fond": "#0f1218", "accent": "#35d0c0", "live": "#ff4d57", "clair": "#161a22", "texte": "#eef2f7", "muted": "#88909e"}, "Plus Jakarta Sans",   {"badge_live": True}),
    ("c6",  "Agenda — Vert institutionnel",     "clair",  {"fond": "#f3f6f3", "accent": "#157a4e", "accentD": "#0f5c3a", "clair": "#ffffff", "texte": "#16261e", "muted": "#6f7c74"}, "Plus Jakarta Sans",   {"timeline": True}),
    ("c7",  "Speakers grid — Ambre",            "clair",  {"fond": "#faf5ee", "accent": "#e07a24", "accentD": "#b85c12", "clair": "#ffffff", "texte": "#241a12", "muted": "#8a7c6c"}, "Plus Jakarta Sans",   {"grille": "portraits"}),
    ("c8",  "Minimal corporate — Bleu",         "clair",  {"fond": "#ffffff", "accent": "#2563eb", "clair": "#f4f6fb", "texte": "#0f1115", "muted": "#767d8c"}, "Plus Jakarta Sans",                       {"aere": True}),
    ("c9",  "Expo / Salon — Sky & ambre",       "sombre", {"fond": "#07131c", "accent": "#15c3e0", "accentD": "#0b9fbd", "highlight": "#ffcb3d", "clair": "#0f2029", "texte": "#eaf6fa", "muted": "#7fa0ad"}, "Plus Jakarta Sans", {"plan": True}),
    ("c10", "Formation / Atelier — Orange",     "clair",  {"fond": "#f6f4ef", "accent": "#e4572e", "accentD": "#bd3f1c", "clair": "#ffffff", "texte": "#201a14", "muted": "#857b6d", "teal": "#1f9e8f"}, "Plus Jakarta Sans", {"places_limitees": True}),
]


def _skin_json(couleurs, police_titre, ornements, mode):
    return {
        "couleurs":  couleurs,
        "polices":   {"titre": police_titre, "corps": "Plus Jakarta Sans"},
        "ornements": ornements,
        "mode":      mode,
    }


def _rows():
    rows = []
    for i, (code, name, mode, couleurs, titre, orn) in enumerate(SKINS, start=1):
        fam = "familial" if code.startswith("b") else "corporate"
        sort = i if fam == "familial" else i - 10
        rows.append(dict(code=code, famille=fam, name=name,
                         skin=_skin_json(couleurs, titre, orn, mode),
                         enveloppe=(fam == "familial"), sort_order=sort))
    return rows


def _sql(r):
    skin = json.dumps(r["skin"], ensure_ascii=False).replace("'", "''")
    name = r["name"].replace("'", "''")
    env = "true" if r["enveloppe"] else "false"
    return (
        "INSERT INTO inv_modeles (cadre_id, famille, name, skin, preview_url, enveloppe, public, sort_order, status, created_at)\n"
        f"SELECT c.id, c.famille, '{name}', '{skin}'::jsonb, NULL, {env}, true, {r['sort_order']}, 'actif', now()\n"
        f"FROM inv_modele_cadres c\n"
        f"WHERE c.code = '{r['code']}' "
        f"AND NOT EXISTS (SELECT 1 FROM inv_modeles m WHERE m.name = '{name}');"
    )


def main(argv):
    rows = _rows()
    if "--print-sql" in argv:
        print("\n".join(_sql(r) for r in rows))
        return
    dry = "--dry-run" in argv
    from core.database import SessionLocal
    from models.invitation import InvModele, InvModeleCadre
    db = SessionLocal()
    created = skipped = 0
    try:
        cadres = {c.code: c for c in db.query(InvModeleCadre).all()}
        names = {m.name for m in db.query(InvModele.name).all()}
        for r in rows:
            cadre = cadres.get(r["code"])
            if cadre is None:
                print(f"  ! cadre {r['code']} absent — lance d'abord seed_inv_cadres.py")
                continue
            if r["name"] in names:
                skipped += 1
                continue
            print(f"  + {r['name']}")
            if not dry:
                db.add(InvModele(cadre_id=cadre.id, famille=cadre.famille, name=r["name"],
                                 skin=r["skin"], enveloppe=r["enveloppe"], public=True,
                                 sort_order=r["sort_order"], status="actif"))
            created += 1
        if not dry:
            db.commit()
        print(f"\n{'[dry-run] ' if dry else ''}gabarits : {created} créés, {skipped} déjà présents, "
              f"{len(rows)} au total.")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
