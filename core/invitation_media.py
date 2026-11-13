"""
core/invitation_media.py — Affiche d'un événement privé (page d'inscription, aperçu de partage).

Sécurité : l'image envoyée par le back-office n'est JAMAIS servie telle quelle. Elle est ouverte
par Pillow (formats JPEG / PNG / WebP uniquement, taille et nombre de pixels bornés), puis
ré-encodée en JPEG : métadonnées (EXIF, GPS…) et contenu non image retirés.
Aperçu de partage : image 1200×630 (format attendu par WhatsApp / Facebook) = affiche entière
au centre sur un fond flou de l'affiche, calculée à la demande et gardée en mémoire.
"""
import base64
import binascii
import hashlib
import io
import re
from collections import OrderedDict
from typing import Tuple

from fastapi import HTTPException
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 4 * 1024 * 1024      # image décodée
MAX_PIXELS = 30_000_000                 # anti « bombe de décompression »
MAX_SIDE = 1600                         # plus grand côté conservé
MIN_SIDE = 200
OG_SIZE = (1200, 630)
_DATA_URL = re.compile(r"^data:image/(jpeg|jpg|png|webp);base64,([A-Za-z0-9+/=\s]+)$")
_ALLOWED = {"JPEG", "PNG", "WEBP"}
_og_cache: "OrderedDict[str, bytes]" = OrderedDict()


def normalize_upload(data_url: str) -> Tuple[bytes, int, int, str]:
    """data:image/…;base64 → (jpeg, largeur, hauteur, sha256). 400 si l'image est refusée."""
    m = _DATA_URL.match((data_url or "").strip())
    if not m:
        raise HTTPException(400, "Affiche : image JPEG, PNG ou WebP attendue.")
    try:
        raw = base64.b64decode(re.sub(r"\s", "", m.group(2)), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Affiche illisible.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Affiche trop lourde (4 Mo maximum).")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            if fmt not in _ALLOWED:
                raise HTTPException(400, "Affiche : format JPEG, PNG ou WebP uniquement.")
            if probe.width * probe.height > MAX_PIXELS:
                raise HTTPException(400, "Affiche trop grande (dimensions).")
            probe.verify()
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)          # photo prise de travers : remise droite
        img.load()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, "Affiche illisible ou corrompue.")
    if min(img.size) < MIN_SIDE:
        raise HTTPException(400, "Affiche trop petite (%d pixels minimum de côté)." % MIN_SIDE)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True, progressive=True)
    jpeg = out.getvalue()
    return jpeg, img.width, img.height, hashlib.sha256(jpeg).hexdigest()


def og_image(jpeg: bytes, sha: str) -> bytes:
    """Aperçu de partage 1200×630 : affiche entière au centre, fond flou. Mémoire : 8 derniers."""
    if sha in _og_cache:
        _og_cache.move_to_end(sha)
        return _og_cache[sha]
    W, H = OG_SIZE
    src = Image.open(io.BytesIO(jpeg)).convert("RGB")
    bg = ImageOps.fit(src, (W, H), Image.LANCZOS, centering=(0.5, 0.3))
    bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(28))).enhance(0.55)
    fg = src.copy()
    fg.thumbnail((W, H), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    out = io.BytesIO()
    bg.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    data = out.getvalue()
    _og_cache[sha] = data
    while len(_og_cache) > 8:
        _og_cache.popitem(last=False)
    return data
