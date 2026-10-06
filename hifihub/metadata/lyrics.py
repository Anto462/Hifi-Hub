"""Lectura y parseo de letras para la vista karaoke.

Las letras se guardan en el tag del archivo. LRCLIB las entrega en formato LRC
sincronizado cuando existe. Aquí se leen del tag y se parsean a una lista de
líneas con tiempo para que la UI resalte en azul la línea actual siguiendo a mpv.
"""

from __future__ import annotations

import re
from pathlib import Path

from hifihub.metadata.tagger import MP3_EXTS, MP4_EXTS

_LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")


def read_lyrics(path: Path | str) -> str | None:
    """Extrae el tag de letras según contenedor."""
    from mutagen import File as MutagenFile

    ext = Path(path).suffix.lower()
    audio = MutagenFile(str(path))
    if audio is None or audio.tags is None:
        return None
    if ext in MP4_EXTS:
        lyr = audio.tags.get("\xa9lyr")
        return str(lyr[0]) if lyr else None
    if ext in MP3_EXTS:
        uslt = audio.tags.getall("USLT")
        return str(uslt[0].text) if uslt else None
    lyr = audio.tags.get("lyrics")  # Vorbis (FLAC/Opus/OGG)
    if isinstance(lyr, list):
        return str(lyr[0]) if lyr else None
    return str(lyr) if lyr else None


def parse_lrc(text: str) -> dict:
    """Parsea letras. Devuelve {'synced': bool, 'lines': [{'t': seg|None, 'text': str}]}.

    Si el texto no tiene marcas [mm:ss.xx] se devuelve como líneas planas.
    """
    lines = []
    synced = False
    for raw in text.splitlines():
        m = _LRC_LINE.match(raw.strip())
        if m:
            synced = True
            minutes, seconds, content = m.groups()
            lines.append({"t": int(minutes) * 60 + float(seconds), "text": content.strip()})
        elif raw.strip():
            lines.append({"t": None, "text": raw.strip()})
    # Ordenar las sincronizadas por tiempo del track (algunos LRC vienen desordenados).
    if synced:
        lines = [ln for ln in lines if ln["t"] is not None]
        lines.sort(key=lambda ln: ln["t"])
    return {"synced": synced, "lines": lines}
