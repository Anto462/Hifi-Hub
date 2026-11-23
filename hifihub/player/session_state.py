"""Sesión de reproducción persistente.

Se guarda la cola anterior en CONFIG_DIR/playback.json: la cola en su orden REAL, la pista, el segundo, el volumen, el aleatorio y la repetición. Al
arrancar la app queda preparada en pausa; suena al pulsar play.

El estado ocupa unos pocos KB, se escribe de forma
temporal con un archivo temporal + uno de reemplazo, así un cierre a medias no lo corrompe y
solo si ha cambiado desde la última escritura.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from hifihub import paths

VERSION = 1
_last_written: dict[str, str] = {}   # ruta -> último JSON escrito (evita escrituras iguales)


def state_file() -> Path:
    return paths.CONFIG_DIR / "playback.json"


def snapshot(queue: dict[str, Any], position: float) -> dict[str, Any] | None:
    """Estado guardable a partir de `controller.get_queue()` y la posición.

    None si no hay nada que guardar o si la cola es una vista previa del Estudio
    (archivos sueltos sin id): esa no debe pisar la sesión real.
    """
    tracks = queue.get("queue") or []
    index = queue.get("index", -1)
    if not tracks or not (0 <= index < len(tracks)):
        return None
    if any(t.get("id") is None for t in tracks):
        return None
    return {
        "version": VERSION,
        "ids": [t["id"] for t in tracks],
        "paths": [t.get("path") for t in tracks],   # respaldo si un id cambia al reescanear
        "index": index,
        "position": round(max(0.0, float(position or 0)), 1),
        "shuffle": bool(queue.get("shuffle")),
        "repeat": queue.get("repeat") or "off",
        "volume": float(queue.get("volume", 100.0)),
        "saved_at": int(time.time()),
    }


def save(state: dict[str, Any], file: Path | None = None) -> bool:
    """Escribe el estado si cambió. Devuelve True si llegó a escribir."""
    file = file or state_file()
    body = dict(state)
    body.pop("saved_at", None)            # la hora no cuenta como cambio
    key = json.dumps(body, sort_keys=True)
    if _last_written.get(str(file)) == key:
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, file)                 # atómico: nunca queda un JSON a medias
    _last_written[str(file)] = key
    return True


def load(file: Path | None = None) -> dict[str, Any] | None:
    """Estado guardado, o None si no hay o está dañado (nunca lanza)."""
    file = file or state_file()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION or not data.get("ids"):
        return None
    return data


def clear(file: Path | None = None) -> None:
    file = file or state_file()
    _last_written.pop(str(file), None)
    try:
        file.unlink()
    except OSError:
        pass


def resolve(lib, state: dict[str, Any]) -> tuple[list, int]:
    """Filas de la biblioteca para la cola guardada y el índice ya corregido.

    Una pista se busca por id y, si no está (p. ej. se reconstruyó la
    biblioteca), por su ruta. Las que ya no existen se omiten; si era la que
    sonaba, se retoma en la siguiente que siga existiendo.
    """
    ids = state.get("ids") or []
    paths_ = state.get("paths") or []
    want = int(state.get("index", 0))
    rows, index = [], 0
    for i, tid in enumerate(ids):
        row = lib.get_track(int(tid)) if tid is not None else None
        if row is None and i < len(paths_) and paths_[i]:
            row = lib.get_by_path(paths_[i])
        if row is None:
            continue
        if i <= want:
            index = len(rows)             # la última superviviente hasta la que sonaba
            if i < want:
                index += 1                # la que sonaba desapareció: la siguiente
        rows.append(row)
    if not rows:
        return [], 0
    return rows, min(index, len(rows) - 1)


def current_entry(lib, state: dict[str, Any]):
    """Solo la fila de la pista que sonaba (para la vista previa al arrancar,
    sin resolver la cola entera ni arrancar libmpv)."""
    ids = state.get("ids") or []
    i = int(state.get("index", 0))
    if not (0 <= i < len(ids)):
        return None
    row = lib.get_track(int(ids[i]))
    if row is None:
        p = (state.get("paths") or [None] * len(ids))[i]
        row = lib.get_by_path(p) if p else None
    return row
