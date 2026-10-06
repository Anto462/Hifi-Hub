"""
Limpieza de títulos de YouTube y extracción de artista/título.

Los títulos de YouTube vienen con ruido no musical ("[Official Video]",
"(Lyrics)", "| Sello", emojis). Este módulo lo elimina PRESERVANDO la
información musical relevante — (Remix), (Acoustic), (Live), (feat. X) se
conservan porque distinguen la grabación.

"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Etiquetas de ruido a eliminar (entre () o []). Se comparan sin distinguir
# mayúsculas. Deliberadamente NO incluye remix/acoustic/live/remaster/feat.
_NOISE_KEYWORDS = [
    r"official\s*(music\s*)?video",
    r"official\s*audio",
    r"official\s*lyric[s]?\s*video",
    r"official\s*visuali[sz]er",
    r"lyric[s]?\s*video",
    r"lyric[s]?",
    r"visuali[sz]er",
    r"audio",
    r"video\s*oficial",
    r"video\s*clip",
    r"m/?v",
    r"mv",
    r"hd",
    r"hq",
    r"4k",
    r"8k",
    r"1080p?",
    r"720p?",
    r"full\s*hd",
    r"color\s*coded",
    r"free\s*download",
    r"out\s*now",
    r"explicit",
]

# (ruido) o [ruido]
_NOISE_RE = re.compile(
    r"[\(\[]\s*(?:" + "|".join(_NOISE_KEYWORDS) + r")\s*[\)\]]",
    re.IGNORECASE,
)

# Corchetes sueltos tipo [NCS Release], [Monstercat], etc. al final.
_TRAILING_BRACKETS_RE = re.compile(r"\s*\[[^\]]*\]\s*$")

# "Título | Sello Discográfico"
_PIPE_TAIL_RE = re.compile(r"\s*\|.*$")

# Emojis y símbolos decorativos comunes.
_EMOJI_RE = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f000-\U0001f0ff" "]+",
    flags=re.UNICODE,
)

_MULTISPACE_RE = re.compile(r"\s{2,}")
# Separador artista/título: guion con espacios, en distintos formatos unicode.
_SPLIT_RE = re.compile(r"\s+[-–—]\s+")


@dataclass(frozen=True)
class ParsedTitle:
    artist: str | None
    title: str


def clean_title(raw: str) -> str:
    """Quita ruido no musical de un título, preservando lo relevante."""
    s = raw
    s = _PIPE_TAIL_RE.sub("", s)
    s = _NOISE_RE.sub("", s)
    s = _EMOJI_RE.sub("", s)
    s = _TRAILING_BRACKETS_RE.sub("", s)
    # Paréntesis/corchetes vacíos que hayan quedado.
    s = re.sub(r"[\(\[]\s*[\)\]]", "", s)
    s = _MULTISPACE_RE.sub(" ", s)
    return s.strip(" -–—\t")


def split_artist_title(raw: str) -> ParsedTitle:
    """Separa 'Artista - Título' si hay un guion claro; si no, todo es título."""
    cleaned = clean_title(raw)
    parts = _SPLIT_RE.split(cleaned, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return ParsedTitle(artist=parts[0].strip(), title=parts[1].strip())
    return ParsedTitle(artist=None, title=cleaned)


def parse(raw_title: str, uploader: str | None = None, artist: str | None = None) -> ParsedTitle:
    """Resuelve artista/título con la mejor información disponible.

    Prioridad de artista: el tag `artist` de yt-dlp > el que aparece en el
    título ("Artista - Título") > el canal (`uploader`, degradando sufijos
    tipo ' - Topic' de los canales auto-generados de YouTube).
    """
    parsed = split_artist_title(raw_title)
    if artist:
        return ParsedTitle(artist=artist.strip(), title=parsed.title)
    if parsed.artist:
        return parsed
    if uploader:
        return ParsedTitle(artist=_clean_uploader(uploader), title=parsed.title)
    return parsed


def _clean_uploader(uploader: str) -> str:
    # Canales "Art - Topic" y "ArtVEVO" de YouTube.
    u = re.sub(r"\s*-\s*Topic$", "", uploader, flags=re.IGNORECASE)
    u = re.sub(r"VEVO$", "", u)
    return u.strip()
