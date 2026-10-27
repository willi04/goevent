"""
core/cms_registry.py — Registre des contenus éditables du site.

Source de vérité = le HTML lui-même : chaque élément balisé
  data-cms="page.NNN"        (texte, valeur par défaut = son texte actuel)
  data-cms-img="page.imgNN"  (image, défaut = son src)
  data-cms-href="clé"        (lien, défaut = son href)
dans web/**/*.html est un champ du back-office. Les composants générés en JS
(footer, drawer, encarts simulateur dans bt-api.js) et les coordonnées sont
déclarés dans GLOBAL_FIELDS ci-dessous.

La base (site_contents) ne stocke que les SURCHARGES : supprimer une surcharge
= revenir au texte du HTML. Le balisage des pages se fait avec
scripts/cms_tag.py (idempotent).
"""
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

LEGAL_PAGES = ("cgu", "confidentialite", "remboursements")

PAGE_TITLES = {
    "global": "Éléments communs (footer, contact, simulateur)",
    "index": "Accueil",
    "event": "Liste des événements",
    "event_detail": "Fiche événement",
    "checkout": "Paiement",
    "a_propos": "À propos",
    "partenaire": "Partenaires & régie",
    "centre_aide": "Centre d'aide (hors FAQ)",
    "feedback": "Feedback",
    "follow": "Follow",
    "login": "Connexion",
    "signup": "Inscription",
    "simulateur": "Simulateur de rentabilité",
    "cgu": "CGU (en-tête et liens)",
    "confidentialite": "Confidentialité (en-tête et liens)",
    "remboursements": "Remboursements (en-tête et liens)",
    "invitations": "Invitations (page publique)",
}

# Champs non balisés dans un fichier HTML (composants JS de bt-api.js, coordonnées).
# kind : text | textarea | url | email | phone | image
GLOBAL_FIELDS = [
    # (clé, section, libellé, kind, défaut)
    ("global.contact.email", "Coordonnées", "Email de contact (footer, centre d'aide)", "email",
     "emmanuel.madoukou30@gmail.com"),
    ("global.contact.whatsapp", "Coordonnées", "Numéro WhatsApp support (format international, ex. 23672259398)",
     "phone", "23672259398"),
    ("invitations.contact.whatsapp", "Contact", "Numéro WhatsApp des devis Invitations (format international ; vide = numéro support)",
     "phone", ""),
    ("global.social.facebook", "Réseaux sociaux", "Page Facebook (lien complet, vide = masqué)", "url", ""),
    ("global.social.instagram", "Réseaux sociaux", "Compte Instagram (lien complet, vide = masqué)", "url", ""),
    ("global.social.tiktok", "Réseaux sociaux", "Compte TikTok (lien complet, vide = masqué)", "url", ""),
    ("global.social.linkedin", "Réseaux sociaux", "Page LinkedIn (lien complet, vide = masqué)", "url", ""),
    ("global.footer.tagline", "Footer", "Texte de présentation", "textarea",
     "La première plateforme de billetterie en ligne en République Centrafricaine. Soutenons ensemble nos artistes."),
    ("global.footer.payment_title", "Footer", "Encadré paiement : titre", "text", "Orange Money"),
    ("global.footer.payment_text", "Footer", "Encadré paiement : texte", "textarea",
     "Payez en toute sécurité avec votre compte Orange Money local."),
    ("global.footer.copyright", "Footer", "Mention de copyright", "text", "© 2026 GoEvent | Tous Droits Réservés."),
    ("global.simulateur.url", "Encarts simulateur", "Lien vers le simulateur", "url",
     "https://goevent.africa/simulateur/"),
    ("global.simulateur.landing_title", "Encarts simulateur", "Accueil : titre", "text", "Simule ta rentabilité"),
    ("global.simulateur.landing_badge", "Encarts simulateur", "Accueil : badge", "text", "Gratuit · 2 min"),
    ("global.simulateur.landing_text", "Encarts simulateur", "Accueil : texte", "text",
     "Avant de te lancer, vois si ton événement est rentable"),
    ("global.simulateur.dash_title", "Encarts simulateur", "Dashboard orga : titre", "text",
     "Ton prochain événement sera-t-il rentable ?"),
    ("global.simulateur.dash_text", "Encarts simulateur", "Dashboard orga : texte", "textarea",
     "Simule tes coûts, tes billets et ton remplissage en 2 minutes. Découvre ton seuil de rentabilité avant de te lancer."),
    ("global.simulateur.dash_cta", "Encarts simulateur", "Dashboard orga : bouton", "text", "Ouvrir le simulateur"),
]

# Montants et pourcentages affichés = engagement commercial (doivent correspondre
# aux tarifs réellement facturés, cf. core/config.py) → admin uniquement.
_FCFA = re.compile(r"\d[\d\s\u00a0\u202f.,]*\s*FCFA")
_PCT = re.compile(r"\d+\s*%")
_TEXTAREA_TAGS = {"p", "li", "dd", "blockquote", "td"}


def _is_admin_only(page: str, default: str) -> bool:
    default = default or ""
    return (page in LEGAL_PAGES or bool(_FCFA.search(default))
            or (page == "partenaire" and bool(_PCT.search(default))))


class _Parser(HTMLParser):
    def __init__(self, fields: dict, order: list):
        super().__init__(convert_charrefs=True)
        self.fields, self.order = fields, order
        self.section = ""
        self.heading = None      # [tag, depth, buffer, nb de champs à l'ouverture]
        self.main_seen = False   # si la page a un <main>, seuls ses titres font foi
        self.in_main = 0
        self.captures = []       # [key, tag, depth, buffer]
        self.depth = 0

    def _add(self, key, kind, default, tag=""):
        if not key or key in self.fields:
            return
        page = key.split(".", 1)[0]
        default = " ".join((default or "").split())
        if kind == "text" and (tag in _TEXTAREA_TAGS or len(default) > 90):
            kind = "textarea"
        self.fields[key] = {
            "key": key, "page": page, "section": self.section or "Haut de page",
            "label": default[:90] or key, "kind": kind, "default": default,
            "admin_only": _is_admin_only(page, default),
        }
        self.order.append(key)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("data-cms-img"):
            self._add(a["data-cms-img"], "image", a.get("src", ""))
        if a.get("data-cms-href"):
            href = a.get("href", "") or ""
            prefix = a.get("data-cms-href-prefix") or ""
            if prefix and href.startswith(prefix):
                href = href[len(prefix):]
            self._add(a["data-cms-href"], "url", "" if href == "#" else href)
        if tag == "br":
            self.handle_data(" ")
        if tag in ("img", "br", "input", "meta", "link", "hr", "source", "wbr"):
            return
        self.depth += 1
        if tag == "main":
            self.main_seen = True
            self.in_main += 1
            self.section = ""  # les titres hors <main> (splash, header) ne comptent pas
        if tag in ("h1", "h2") and self.heading is None and (self.in_main or not self.main_seen):
            self.heading = [tag, self.depth, [], len(self.order)]
        if a.get("data-cms"):
            self.captures.append([a["data-cms"], tag, self.depth, []])

    def handle_endtag(self, tag):
        if tag in ("img", "br", "input", "meta", "link", "hr", "source", "wbr"):
            return
        for cap in list(self.captures):
            if cap[2] >= self.depth:
                self._add(cap[0], "text", "".join(cap[3]), cap[1])
                self.captures.remove(cap)
        if self.heading and self.heading[1] >= self.depth:
            txt = " ".join("".join(self.heading[2]).split())
            if txt:
                self.section = txt[:80]
                # champs capturés DANS le titre (titre en plusieurs segments) → cette section
                for k in self.order[self.heading[3]:]:
                    if self.fields[k]["page"] != "global":
                        self.fields[k]["section"] = self.section
            self.heading = None
        if tag == "main":
            self.in_main = max(0, self.in_main - 1)
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        for cap in self.captures:
            cap[3].append(data)
        if self.heading is not None:
            self.heading[2].append(data)


@lru_cache(maxsize=1)
def get_registry(web_dir: str = str(WEB_DIR)) -> dict:
    """{key: field} dans l'ordre d'apparition (dict ordonné)."""
    fields, order = {}, []
    for key, section, label, kind, default in GLOBAL_FIELDS:
        fields[key] = {"key": key, "page": key.split(".", 1)[0], "section": section, "label": label,
                       "kind": kind, "default": default, "admin_only": False}
        order.append(key)
    root = Path(web_dir)
    if root.exists():
        files = sorted(root.rglob("*.html"), key=lambda p: (p.parent != root, str(p)))
        for path in files:
            try:
                src = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "data-cms" not in src:
                continue
            src = re.sub(r"<script\b[\s\S]*?</script>", "", src)  # attributs en JS = hors registre
            p = _Parser(fields, order)
            p.feed(src)
            p.close()
    return {k: fields[k] for k in order}


def pages_summary(registry: dict) -> list:
    counts = {}
    for f in registry.values():
        counts[f["page"]] = counts.get(f["page"], 0) + 1
    ordered = [p for p in PAGE_TITLES if p in counts] + sorted(p for p in counts if p not in PAGE_TITLES)
    return [{"page": p, "title": PAGE_TITLES.get(p, p), "count": counts[p],
             "legal": p in LEGAL_PAGES} for p in ordered]
