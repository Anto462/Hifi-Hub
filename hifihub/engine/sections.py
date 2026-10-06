"""Descargar solo bloques de tiempo de un video largo.

Formatos de marca aceptados: "SS", "MM:SS", "HH:MM:SS" (con ".ms" opcional).
Un rango es "inicio-fin"; "inicio-" significa hasta el final.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TS_RE = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")


class SectionError(ValueError):
    pass


@dataclass(frozen=True)
class TimeRange:
    start: float
    end: float | None  # None = hasta el final

    def label(self) -> str:
        return f"{format_timestamp(self.start)}-{format_timestamp(self.end) if self.end else 'fin'}"


def parse_timestamp(text: str) -> float:
    """'1:23:45.5' -> segundos. Acepta SS, MM:SS y HH:MM:SS."""
    text = text.strip()
    m = _TS_RE.match(text)
    if not m:
        raise SectionError(f"Marca de tiempo no válida: {text!r} (usa HH:MM:SS, MM:SS o SS)")
    h, mnt, s = m.groups()
    seconds = float(s)
    if h is not None and float(s) >= 60:
        raise SectionError(f"Marca de tiempo no válida: {text!r} (segundos >= 60)")
    if h is not None and mnt is not None:      # HH:MM:SS
        if int(mnt) >= 60:
            raise SectionError(f"Marca de tiempo no válida: {text!r} (minutos >= 60)")
        seconds += int(h) * 3600 + int(mnt) * 60
    elif h is not None:                        # MM:SS (solo un ':')
        seconds += int(h) * 60
    return seconds


def format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_range(text: str) -> TimeRange:
    """'12:00-15:30' -> TimeRange. 'inicio-' = hasta el final."""
    if "-" not in text:
        raise SectionError(f"Rango no válido: {text!r} (usa inicio-fin, ej. 1:23:00-1:27:30)")
    start_txt, _, end_txt = text.partition("-")
    start = parse_timestamp(start_txt) if start_txt.strip() else 0.0
    end = parse_timestamp(end_txt) if end_txt.strip() else None
    if end is not None and end <= start:
        raise SectionError(f"Rango no válido: {text!r} (el fin debe ser posterior al inicio)")
    return TimeRange(start=start, end=end)


def parse_ranges(texts: list[str] | str) -> list[TimeRange]:
    """Lista de rangos, o un string con rangos separados por comas."""
    if isinstance(texts, str):
        texts = [t for t in texts.split(",") if t.strip()]
    return [parse_range(t) for t in texts]


def apply_sections(opts: dict[str, Any], ranges: list[TimeRange], exact: bool = False) -> None:
    """Inyecta los rangos en las opciones de yt-dlp.

    - exact=False (default purista): corta en los keyframes/segmentos existentes
      SIN recodificar. Bit-perfect dentro del rango, pero el inicio puede
      adelantarse hasta el keyframe anterior.
    - exact=True: fuerza keyframes en los cortes -> precisión al segundo a costa
      de recodificar el tramo.
    - El outtmpl gana el sufijo de sección para que varios cortes del mismo
      video no se sobrescriban entre sí.
    """
    if not ranges:
        return
    from yt_dlp.utils import download_range_func

    opts["download_ranges"] = download_range_func(
        None, [(r.start, r.end if r.end is not None else float("inf")) for r in ranges]
    )
    opts["force_keyframes_at_cuts"] = bool(exact)

    outtmpl = opts.get("outtmpl")
    if isinstance(outtmpl, str) and "%(section_start" not in outtmpl:
        stem, dot, ext = outtmpl.rpartition(".")
        if dot:
            opts["outtmpl"] = f"{stem} [%(section_start)d-%(section_end)d].{ext}"


def chapter_postprocessor() -> dict[str, Any]:
    """División por capítulos un DJ set con tracklist en capítulos
    sale ya troceado y nombrado. """
    return {"key": "FFmpegSplitChapters", "force_keyframes": False}


CHAPTER_OUTTMPL = "%(title)s/%(section_number)02d - %(section_title)s.%(ext)s"
