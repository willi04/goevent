"""
scripts/cms_tag.py — Balisage CMS des pages publiques.

Ajoute data-cms="<page>.<n>" sur chaque texte « éditorial » du contenu
principal (<main>) des pages listées dans PAGES, et data-cms-img="<page>.imgNN"
sur leurs images. Le texte/l'image présents dans le HTML restent la VALEUR
PAR DÉFAUT : le back-office ne stocke que les surcharges (table site_contents),
appliquées côté navigateur par web/cms.js.

Idempotent : un élément déjà balisé n'est jamais re-balisé, et la numérotation
reprend après le plus grand numéro existant de la page. À relancer après avoir
ajouté du texte dans une page :

    python scripts/cms_tag.py            # balise
    python scripts/cms_tag.py --dry-run  # affiche sans écrire

Ce qui n'est PAS balisé (volontairement) :
  - tout ce qui est hors <main> (navigation, drawer, header) ;
  - les éléments avec un id, et le contenu des conteneurs pilotés par JS
    (ids référencés dans les <script> de la page) : le JS réécrirait la valeur ;
  - les icônes (material-symbols), les textes sans lettre (compteurs, « … ») ;
  - les zones gérées autrement : FAQ (#faq-container), corps des pages
    légales (.legal-content), date de mise à jour des pages légales,
    prix (data-price : pilotés par les Tarifs du back-office) et emplacements
    (data-zone : blocs du back-office).
Les fins de ligne du fichier (CRLF/LF) sont préservées.
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"

# fichier -> (préfixe de clé, ids à exclure en plus, classes à exclure, ids JS autorisés)
PAGES = {
    "index.html": ("index", set(), set(), {"hero-slides-track", "hero-carousel"}),
    "a_propos.html": ("a_propos", set(), set(), set()),
    "partenaire.html": ("partenaire", set(), set(), set()),
    "centre_aide.html": ("centre_aide", {"faq-container", "category-filters"}, set(), set()),
    "cgu.html": ("cgu", set(), {"legal-content"}, set()),
    "confidentialite.html": ("confidentialite", set(), {"legal-content"}, set()),
    "remboursements.html": ("remboursements", set(), {"legal-content"}, set()),
    "event.html": ("event", set(), set(), set()),
    "event_detail.html": ("event_detail", set(), set(), set()),
    "checkout.html": ("checkout", set(), set(), set()),
    "login.html": ("login", set(), set(), set()),
    "signup.html": ("signup", set(), set(), set()),
    "feedback.html": ("feedback", set(), set(), set()),
    "follow.html": ("follow", set(), set(), set()),
    "simulateur/index.html": ("simulateur", set(), set(), set()),
}

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
SKIP_SUBTREE = {"script", "style", "svg", "noscript", "select", "textarea", "template", "head"}
TEXT_HOSTS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "a", "button", "span", "li",
              "label", "strong", "em", "b", "small", "td", "th", "dt", "dd",
              "summary", "figcaption", "blockquote", "div"}
LEGAL_DATE = re.compile(r"Derni[eè]re mise [àa] jour", re.I)


def js_ids(src: str) -> set:
    scripts = "\n".join(re.findall(r"<script\b[^>]*>([\s\S]*?)</script>", src))
    ids = set(re.findall(r"getElementById\(\s*[\"'`]([\w-]+)", scripts))
    ids |= set(re.findall(r"querySelector(?:All)?\(\s*[\"'`]#([\w-]+)", scripts))
    ids |= set(re.findall(r"\$\(\s*[\"'`]([\w-]+)[\"'`]\s*\)", scripts))  # helper $('id') du simulateur
    return ids


class Node:
    __slots__ = ("tag", "attrs", "start", "end_of_start", "texts", "children",
                 "excluded", "skip_self", "outside")

    def __init__(self, tag, attrs, start, end_of_start):
        self.tag, self.attrs = tag, attrs
        self.start, self.end_of_start = start, end_of_start
        self.texts = []      # (abs_offset, raw_text)
        self.children = 0
        self.excluded = False   # sous-arbre exclu
        self.skip_self = False  # élément non balisable lui-même (id, icône…)
        self.outside = False    # hors <main>


class Tagger(HTMLParser):
    def __init__(self, src, prefix, extra_ids, extra_classes, allowed_js_ids, require_main=True):
        super().__init__(convert_charrefs=False)
        self.src = src
        self.prefix = prefix
        self.line_starts = [0]
        for m in re.finditer("\n", src):
            self.line_starts.append(m.end())
        self.excl_ids = (js_ids(src) - allowed_js_ids) | extra_ids
        self.excl_classes = extra_classes
        self.require_main = require_main
        self.in_main = 0 if require_main else 1
        self.stack = []
        self.inserts = []  # (offset, text)
        nums = [int(n) for n in re.findall(r'data-cms="%s\.(\d+)"' % re.escape(prefix), src)]
        imgs = [int(n) for n in re.findall(r'data-cms-img="%s\.img(\d+)"' % re.escape(prefix), src)]
        self.n = max(nums or [0])
        self.ni = max(imgs or [0])
        self.tagged = []

    def abspos(self):
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def _excluded_context(self):
        return any(n.excluded for n in self.stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        start = self.abspos()
        raw = self.get_starttag_text()
        end_of_start = start + len(raw) - (2 if raw.endswith("/>") else 1)
        if tag == "main":
            self.in_main += 1
        if tag == "img":
            self._img(a, end_of_start)
            return
        if tag in VOID:
            return
        node = Node(tag, a, start, end_of_start)
        classes = set((a.get("class") or "").split())
        if (tag in SKIP_SUBTREE or a.get("id") in self.excl_ids
                or classes & self.excl_classes
                or "data-cms-faq" in a or "data-cms-page" in a):
            node.excluded = True
        node.outside = not self.in_main
        if a.get("id") or "material-symbols-outlined" in classes or "material-icons" in classes \
                or "data-cms" in a or "data-cms-page-date" in a or "data-price" in a \
                or tag not in TEXT_HOSTS:
            node.skip_self = True
        if self.stack:
            self.stack[-1].children += 1
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def _img(self, a, end_of_start):
        if not self.in_main or self._excluded_context():
            return
        if "data-cms-img" in a or a.get("id") or (a.get("src") or "").startswith("data:"):
            return
        self.ni += 1
        key = "%s.img%02d" % (self.prefix, self.ni)
        self.inserts.append((end_of_start, ' data-cms-img="%s"' % key))
        self.tagged.append((key, "IMG " + (a.get("src") or "")[:60]))
        if self.stack:
            self.stack[-1].children += 1

    def _add_text(self, raw):
        if not self.stack:
            return
        pos = self.abspos()
        texts = self.stack[-1].texts
        if texts and texts[-1][0] + len(texts[-1][1]) == pos:
            # « Sécurisé &amp; local » : on fusionne texte + entités contigus
            texts[-1] = (texts[-1][0], texts[-1][1] + raw)
        elif raw.strip():
            texts.append((pos, raw))

    def handle_data(self, data):
        self._add_text(data)

    def handle_entityref(self, name):
        self._add_text("&%s;" % name)

    def handle_charref(self, name):
        self._add_text("&#%s;" % name)

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = max(0, self.in_main - 1)
        if tag in VOID:
            return
        idx = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                idx = i
                break
        if idx is None:
            return
        while len(self.stack) > idx:
            node = self.stack.pop()
            self._finalize(node)

    def _finalize(self, node):
        if node.excluded or node.outside or self._excluded_context():
            return
        meaningful = [(o, t) for o, t in node.texts if self._meaningful(t)]
        if not meaningful or node.attrs.get("id"):
            return
        if "data-cms-page-date" in node.attrs:
            return
        if self.prefix in ("cgu", "confidentialite", "remboursements") and \
                any(LEGAL_DATE.search(t) for _, t in meaningful):
            # date de mise à jour → pilotée par la révision de la page légale
            self.inserts.append((node.end_of_start, " data-cms-page-date"))
            return
        if node.children == 0 and not node.skip_self:
            self.n += 1
            key = "%s.%03d" % (self.prefix, self.n)
            self.inserts.append((node.end_of_start, ' data-cms="%s"' % key))
            self.tagged.append((key, " ".join(" ".join(t.split()) for _, t in meaningful)[:70]))
            return
        if node.children > 0 and node.tag in TEXT_HOSTS and "data-cms" not in node.attrs \
                and "material-symbols-outlined" not in (node.attrs.get("class") or ""):
            # contenu mixte : on enveloppe chaque segment de texte direct dans un <span>
            for off, raw in meaningful:
                lead = len(raw) - len(raw.lstrip())
                core = raw.strip()
                self.n += 1
                key = "%s.%03d" % (self.prefix, self.n)
                s = off + lead
                self.inserts.append((s, '<span data-cms="%s">' % key))
                self.inserts.append((s + len(core), "</span>"))
                self.tagged.append((key, " ".join(core.split())[:70]))

    @staticmethod
    def _meaningful(t):
        t = " ".join(t.split())
        return len(t) >= 2 and re.search(r"[A-Za-zÀ-ÿ]", t) and t.lower() not in ("chargement...", "chargement…")


def tag_file(rel, prefix, extra_ids, extra_classes, allowed, dry=False):
    path = WEB / rel
    with open(path, encoding="utf-8", newline="") as f:
        src = f.read()
    t = Tagger(src, prefix, extra_ids, extra_classes, allowed,
               require_main=("<main" in src))
    t.feed(src)
    t.close()
    out = src
    # insertions de la fin vers le début ; à offset égal, ordre stable inverse
    for off, txt in sorted(t.inserts, key=lambda x: x[0], reverse=True):
        out = out[:off] + txt + out[off:]
    if not dry and out != src:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(out)
    return t.tagged


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    total = 0
    for rel, (prefix, ids, classes, allowed) in PAGES.items():
        tagged = tag_file(rel, prefix, ids, classes, allowed, dry)
        total += len(tagged)
        print("%-24s %3d élément(s) balisé(s)" % (rel, len(tagged)))
        if dry:
            for k, txt in tagged:
                print("    %-18s %s" % (k, txt))
    print("Total :", total)
