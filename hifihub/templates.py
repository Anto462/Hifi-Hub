"""Plantillas de organización jerárquica (outtmpl de yt-dlp).

Usa la sintaxis de *campos con fallback* de yt-dlp — `%(artist,uploader)s` —
porque muchos videos no traen `artist` y hay que degradar a `uploader` para no
generar carpetas "NA".
"""

from __future__ import annotations

import os
from pathlib import Path

# Plantillas relativas a la carpeta raíz elegida por el usuario.
TEMPLATES: dict[str, str] = {
    # Playlist genérica: carpeta por lista, prefijo con índice de 2 dígitos.
    "playlist": "%(playlist_title)s/%(playlist_index)02d - %(artist,uploader)s - %(title)s.%(ext)s",
    # Discografía/álbum: Artista/Álbum/NN - Título.
    "album": "%(artist,uploader)s/%(album,playlist_title)s/%(track_number,playlist_index)02d - %(title)s.%(ext)s",
    # Pistas sueltas: todo bajo Singles/.
    "single": "Singles/%(artist,uploader)s - %(title)s.%(ext)s",
    # Sin jerarquía: el comportamiento del script original.
    "flat": "%(title)s.%(ext)s",
}

DEFAULT_TEMPLATE = "playlist"

# Límite práctico de ruta en Windows sin habilitar LongPaths.
WINDOWS_MAX_PATH = 260


def get_template(name: str) -> str:
    try:
        return TEMPLATES[name]
    except KeyError:
        raise ValueError(
            f"Plantilla desconocida: {name!r}. Opciones: {', '.join(TEMPLATES)}"
        ) from None


def build_outtmpl(root: Path | str, template: str = DEFAULT_TEMPLATE) -> str:
    """Combina la carpeta raíz con una plantilla (por nombre o literal)."""
    pattern = TEMPLATES.get(template, template)
    return os.path.join(str(root), pattern)


def is_custom(template: str) -> bool:
    """True si `template` es un patrón literal en vez de un preset conocido."""
    return template not in TEMPLATES
