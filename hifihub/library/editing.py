"""
Edición manual de metadatos.

Editar escribe primero los tags en el archivo (con el tagger multi-contenedor) y
después reconstruye la fila leyéndola del archivo (scanner). Así la corrección:
- sobrevive a un re-escaneo posterior,
- la ven también otros reproductores (Foobar, Roon, el Explorador).

Campos editables: título, artista, álbum, artista del álbum, nº de pista, género,
año. Un campo vacío se interpreta como "sin cambio" para no borrar datos por error.
"""

from __future__ import annotations

from pathlib import Path

from hifihub.library import scanner
from hifihub.library.db import Library
from hifihub.metadata.tagger import TrackTags, write_tags

EDITABLE_FIELDS = ("title", "artist", "album", "album_artist", "track_number", "genre", "date")


class EditError(RuntimeError):
    pass


def _clean(fields: dict) -> dict:
    """Solo campos editables con valor no vacío (vacío = no tocar)."""
    out: dict = {}
    for key in EDITABLE_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if key == "track_number":
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            out[key] = str(value).strip()
    return out


def apply_tag_edit(lib: Library, track_id: int, fields: dict) -> dict:
    """Escribe los tags editados al archivo y sincroniza la BD. Devuelve la fila
    actualizada como dict. Lanza EditError si el archivo no se puede escribir
    (p. ej. lo tiene abierto el reproductor: hay que detenerlo antes)."""
    row = lib.get_track(int(track_id))
    if row is None:
        raise EditError("La pista no está en la biblioteca.")
    path = Path(row["path"])
    if not path.exists():
        raise EditError(f"El archivo ya no existe: {path}")

    changes = _clean(fields)
    if not changes:
        return dict(row)

    try:
        write_tags(path, TrackTags(**changes))
    except PermissionError as e:
        raise EditError(
            "No se pudo escribir el archivo (¿lo está reproduciendo la app?). "
            "Detén la reproducción e inténtalo de nuevo."
        ) from e
    except Exception as e:  # noqa: BLE001
        raise EditError(f"No se pudieron escribir los tags: {e}") from e

    # Fuente de verdad: releer el archivo y refrescar la fila (mantiene id).
    track = scanner._build_track(path, path.stat().st_mtime)
    lib.upsert_track(track)
    updated = lib.get_by_path(str(path))
    return dict(updated) if updated else dict(row)
