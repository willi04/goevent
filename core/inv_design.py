"""core/inv_design.py — Assistant visuel des invitations (lot 6).

Deux utilitaires, sans dépendance lourde (Pillow déjà présent via reportlab) :

1. extract_palette(bytes) : extrait une palette de COULEURS d'une affiche
   (fond, accent, clair, texte, muted) pour pré-remplir le skin. On ne
   reconstruit JAMAIS la mise en page — seulement les couleurs. L'admin valide.

2. reencode_image(bytes) : ré-encode une image uploadée (photo client, logo)
   en JPEG/PNG propre, redimensionnée, métadonnées retirées. Toute image
   uploadée est une donnée NON FIABLE : on ne sert jamais les octets d'origine.
"""
import hashlib
import io

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 8 * 1024 * 1024        # 8 Mo : refus au-delà (anti-DoS)
_PALETTE_SAMPLE = 120                      # on travaille sur une vignette
_PHOTO_MAX = 1400                          # côté max d'une photo servie
_LOGO_MAX = 600


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


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)      # respecte l'orientation, puis on jette l'EXIF
    return im


def extract_palette(data: bytes) -> dict:
    """Retourne {fond, accent, accentD, clair, texte, muted} en hex.
    Heuristique : couleurs dominantes d'une vignette, triées par usage ;
    accent = la plus saturée parmi les dominantes ; fond = la plus claire ;
    texte = contraste lisible. L'admin peut tout ajuster derrière."""
    im = _open(data).convert("RGB")
    im.thumbnail((_PALETTE_SAMPLE, _PALETTE_SAMPLE))
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
    kind='logo' garde la transparence (PNG) ; 'photo' sort en JPEG."""
    im = _open(data)
    mx = _LOGO_MAX if kind == "logo" else _PHOTO_MAX
    im.thumbnail((mx, mx))
    out = io.BytesIO()
    if kind == "logo" and im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        im.save(out, format="PNG", optimize=True)
        mime = "image/png"
    else:
        im = im.convert("RGB")
        im.save(out, format="JPEG", quality=82, optimize=True)
        mime = "image/jpeg"
    b = out.getvalue()
    return b, mime, hashlib.sha256(b).hexdigest(), im.width, im.height
