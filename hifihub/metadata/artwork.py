"""
Preparación e incrustación de portadas.

Aquí se recortan las miniaturas o portadas de albumnes y canciones a un cuadrado centrado y se normalizan a JPEG antes de incrustarlas.
"""

from __future__ import annotations

import io
from pathlib import Path

import requests
from PIL import Image

from hifihub.metadata import tagger

_TIMEOUT = 15


def prepare_cover(image_bytes: bytes, size: int = 1200, quality: int = 90) -> bytes:
    """Recorta la imagen a cuadrado centrado y la devuelve como JPEG.

    No amplía si la fuente es más pequeña que `size` (evita interpolar de más).
    """
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    if side > size:
        img = img.resize((size, size), Image.LANCZOS)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=quality)
    return out.getvalue()


def fetch_image(url: str) -> bytes:
    resp = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def embed_from_bytes(audio_path: Path | str, image_bytes: bytes, size: int = 1200) -> None:
    tagger.embed_cover(audio_path, prepare_cover(image_bytes, size))


def embed_from_url(audio_path: Path | str, url: str, size: int = 1200) -> bool:
    """Descarga, procesa e incrusta una portada. Devuelve False si falla la red
    (sin abortar la descarga: la portada es un extra, no un requisito)."""
    try:
        raw = fetch_image(url)
        embed_from_bytes(audio_path, raw, size)
        return True
    except (requests.RequestException, OSError):
        return False
