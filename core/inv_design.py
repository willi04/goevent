"""core/inv_design.py — Assistant visuel des invitations (lot 6).

Deux utilitaires, sans dépendance lourde (Pillow déjà présent via reportlab) :

1. extract_palette(bytes) : extrait une palette de COULEURS d'une affiche
   (fond, accent, clair, texte, muted) pour pré-remplir le skin. On ne
   reconstruit JAMAIS la mise en page — seulement les couleurs. L'admin valide.

2. reencode_image(bytes) : ré-encode une image uploadée (photo client, logo)
   en JPEG/PNG propre, redimensionnée, métadonnées retirées. Toute image
   uploadée est une donnée NON FIABLE : on ne sert jamais les octets d'origine.

3. sanitize_fiche / sanitize_theme / sanitize_vip : contenu saisi au back-office,
   typé et borné avant stockage (rendu HTML côté invité).
"""
import hashlib
import io
import re

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 8 * 1024 * 1024        # 8 Mo : refus au-delà (anti-DoS)
MAX_PIXELS = 30_000_000                    # anti « bombe de décompression » (comme invitation_media)
_ALLOWED = ("JPEG", "PNG", "WEBP")
_PALETTE_SAMPLE = 120                      # on travaille sur une vignette
_PHOTO_MAX = 1400                          # côté max d'une photo servie
_LOGO_MAX = 600
_SPEAKER_MAX = 500                         # photo d'intervenant (affichée en rond ≤ 84 px, écrans x3)
MEDIA_REF = re.compile(r"^m[0-9]{1,10}$")  # référence d'image dans la fiche : « m<id> » (inv_design_media)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _lum(rgb) -> float:
    # luminance perçue (0=noir, 255=blanc)
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _sat(rgb) -> float:
    mx, mn = max(rgb), min(rgb)
    return 0.0 if mx == 0 else (mx - mn) / mx


def _mix(rgb, target, t):
    return tuple(rgb[i] + (target[i] - rgb[i]) * t for i in range(3))


class ImageRejected(Exception):
    """Image refusée avant décodage (format, dimensions) : message affichable."""


def _open(data: bytes, target: int) -> Image.Image:
    """Ouvre une image NON FIABLE. Format et dimensions contrôlés sur l'en-tête, AVANT décodage :
    une image de 12 000 × 12 000 px pèse quelques Ko compressée mais ~576 Mo décodée (instance
    Render 512 Mo). JPEG : décodage directement à l'échelle utile (draft), sans pleine résolution."""
    try:
        im = Image.open(io.BytesIO(data), formats=_ALLOWED)
    except Exception:
        raise ImageRejected("Image illisible : envoyez un JPEG, PNG ou WebP valide")
    if im.width * im.height > MAX_PIXELS:
        raise ImageRejected("Image trop grande (%d mégapixels maximum)" % (MAX_PIXELS // 1_000_000))
    if im.format == "JPEG":
        im.draft(im.mode, (target, target))
    return ImageOps.exif_transpose(im)    # respecte l'orientation, puis on jette l'EXIF


def extract_palette(data: bytes) -> dict:
    """Retourne {fond, accent, accentD, clair, texte, muted} en hex.
    Heuristique : couleurs dominantes d'une vignette, triées par usage ;
    accent = la plus saturée parmi les dominantes ; fond = la plus claire ;
    texte = contraste lisible. L'admin peut tout ajuster derrière."""
    im = _open(data, _PALETTE_SAMPLE)
    im.thumbnail((_PALETTE_SAMPLE, _PALETTE_SAMPLE))   # vignette AVANT conversion (mémoire)
    im = im.convert("RGB")
    q = im.quantize(colors=8, method=Image.Quantize.MEDIANCUT).convert("RGB")
    pal = sorted(q.getcolors(maxcolors=4096) or [], reverse=True)   # [(count, (r,g,b)), …] du + fréquent au -
    cols = [c for _, c in pal] or [(240, 240, 240)]

    dominant = cols[0]                        # couleur la plus présente = fond probable
    light = max(cols, key=_lum)
    dark = min(cols, key=_lum)
    # accent = la plus SATURÉE (hors quasi-gris), la fréquence ne départage que les ex æquo
    sats = [c for c in cols if _sat(c) > 0.18]
    accent = max(sats, key=lambda c: (round(_sat(c), 2), -cols.index(c))) if sats else dark

    fond_is_dark = _lum(dominant) < 110       # l'affiche est globalement sombre
    if fond_is_dark:
        fond = _mix(dominant, (0, 0, 0), 0.10)
        clair = _mix(fond, (255, 255, 255), 0.08)
        texte = (238, 240, 244)
        muted = _mix(texte, fond, 0.45)
        if _sat(accent) < 0.2 or _lum(accent) < 60:   # pas d'accent lisible → bleu par défaut
            accent = (77, 163, 255)
    else:
        base = dominant if _lum(dominant) > 150 else light
        fond = _mix(base, (255, 255, 255), 0.28)
        clair = _mix(base, (255, 255, 255), 0.58)
        texte = _mix(dark, (0, 0, 0), 0.25) if _lum(dark) < 120 else (34, 30, 26)
        muted = _mix(texte, fond, 0.42)

    accentD = _mix(accent, (0, 0, 0), 0.22)
    return {
        "fond": _hex(fond), "accent": _hex(accent), "accentD": _hex(accentD),
        "clair": _hex(clair), "texte": _hex(texte), "muted": _hex(muted),
    }


def reencode_image(data: bytes, kind: str = "photo") -> tuple:
    """Ré-encode une image uploadée. Retourne (octets, mime, sha256, w, h).
    kind='logo' garde la transparence (PNG) ; 'photo' et 'intervenant' sortent en JPEG."""
    mx = {"logo": _LOGO_MAX, "intervenant": _SPEAKER_MAX}.get(kind, _PHOTO_MAX)
    im = _open(data, mx)
    im.thumbnail((mx, mx))
    out = io.BytesIO()
    if kind == "logo" and im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        im.save(out, format="PNG", optimize=True)
        mime = "image/png"
    else:
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))   # transparence → fond blanc (pas noir)
            bg.paste(im, mask=im.split()[-1])
            im = bg
        im = im.convert("RGB")
        im.save(out, format="JPEG", quality=82, optimize=True)
        mime = "image/jpeg"
    b = out.getvalue()
    return b, mime, hashlib.sha256(b).hexdigest(), im.width, im.height


# ── Validation du contenu saisi au back-office (données NON fiables) ─────────
# Le rendu invité (web/invitation.html) assemble du HTML : tout ce qui y arrive est
# typé ici, côté serveur. Clés inconnues ignorées, types et longueurs bornés.
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
COLOR_KEYS = ("fond", "accent", "accentD", "clair", "texte", "muted", "highlight", "accent2", "teal", "sage")
FONTS = ("Plus Jakarta Sans", "Fraunces", "Cormorant Garamond", "Playfair Display", "Marcellus")
_TEXT_MAX = {"text": 300, "textarea": 2000}
_LIST_MAX_ITEMS = 40
_LIST_CELL_MAX = 300
# Colonnes d'image dans les listes (intervenants[].photo, sponsors[].logo) : jamais d'URL
# (fuite d'IP des invités vers des tiers) ; seule une référence « m<id> » vers une image
# téléversée pour CET événement (inv_design_media) est gardée.
_LIST_IMAGE_KEYS = ("photo", "logo")


def _txt(v, n: int, label: str) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str):
        raise ValueError("%s : texte attendu" % label)
    v = _CTRL.sub("", v).strip()
    if len(v) > n:
        raise ValueError("%s : %d caractères maximum" % (label, n))
    return v


def _list_keys(kind: str):
    m = re.match(r"^list\[(.+)\]$", kind or "")
    return [k.strip() for k in m.group(1).split(",")] if m else None


def sanitize_fiche(fiche, champs: dict, media_refs=frozenset()) -> dict:
    """Fiche typée selon `champs` du cadre : text / textarea / list[a,b,c]. Le reste est ignoré.
    `media_refs` : références « m<id> » des images de l'événement (autres valeurs d'image ignorées)."""
    if fiche is None:
        return {}
    if not isinstance(fiche, dict):
        raise ValueError("Fiche : format invalide")
    out = {}
    for key, kind in (champs or {}).items():
        if key not in fiche or kind == "image":
            continue
        cols = _list_keys(kind)
        if cols is None:
            val = _txt(fiche[key], _TEXT_MAX.get(kind, 300), key)
            if val:
                out[key] = val
            continue
        rows = fiche[key] or []
        if not isinstance(rows, list):
            raise ValueError("%s : liste attendue" % key)
        if len(rows) > _LIST_MAX_ITEMS:
            raise ValueError("%s : %d lignes maximum" % (key, _LIST_MAX_ITEMS))
        clean = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("%s : format de ligne invalide" % key)
            r = {}
            for c in cols:
                if c not in row:
                    continue
                if c in _LIST_IMAGE_KEYS:
                    if isinstance(row[c], str) and row[c] in media_refs:
                        r[c] = row[c]
                    continue
                v = _txt(row[c], _LIST_CELL_MAX, "%s (%s)" % (key, c))
                if v:
                    r[c] = v
            if any(k not in _LIST_IMAGE_KEYS for k in r):      # une image seule ne fait pas une ligne
                clean.append(r)
        if clean:
            out[key] = clean
    return out


def sanitize_theme(theme):
    """Palette ajustée : couleurs hex #rrggbb (clés connues) + polices de la liste blanche."""
    if theme is None:
        return None
    if not isinstance(theme, dict):
        raise ValueError("Palette : format invalide")
    out = {}
    cols = theme.get("couleurs")
    if cols is not None:
        if not isinstance(cols, dict):
            raise ValueError("Palette : format invalide")
        out["couleurs"] = {}
        for k, v in cols.items():
            if k not in COLOR_KEYS:
                continue
            if not isinstance(v, str) or not _HEX.match(v):
                raise ValueError("Couleur « %s » invalide (format #rrggbb)" % k)
            out["couleurs"][k] = v.lower()
    pol = theme.get("polices")
    if pol is not None:
        if not isinstance(pol, dict):
            raise ValueError("Polices : format invalide")
        out["polices"] = {}
        for k in ("titre", "corps"):
            if k in pol:
                if pol[k] not in FONTS:
                    raise ValueError("Police « %s » non disponible" % pol[k])
                out["polices"][k] = pol[k]
    return out or None


def sanitize_vip(vip):
    """Options VIP (corporate) : libellé du salon + accès prioritaire."""
    if vip is None:
        return None
    if not isinstance(vip, dict):
        raise ValueError("VIP : format invalide")
    out = {}
    if "salon_label" in vip:
        lab = _txt(vip["salon_label"], 80, "Salon VIP")
        if lab:
            out["salon_label"] = lab
    if "acces_prioritaire" in vip:
        out["acces_prioritaire"] = bool(vip["acces_prioritaire"])
    return out or None
