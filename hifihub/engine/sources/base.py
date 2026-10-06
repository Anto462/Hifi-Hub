"""Contrato común a todas las fuentes de descarga.

Cada fuente vive en su propio módulo y NO importa a las demás: si el extractor de
una se rompe (YouTube cambia su player, archive.org cambia su HTML), las otras
siguen funcionando. Este módulo solo define los tipos que se intercambian.

Las primitivas de calidad (`Quality`) y de emparejamiento viven en
`engine.multisource` y se reexportan aquí para que las fuentes tengan un único
criterio de "qué es mejor audio".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hifihub.engine.multisource import (  # noqa: F401  (reexport intencionado)
    LOSSLESS_CODECS,
    Quality,
    matches,
    normalize_codec,
    title_similarity,
)


class SourceError(Exception):
    """Fallo propio de una fuente (red, formato inesperado, sin resultados).

    Se captura por fuente para que el fallo de una no aborte al resto.
    """


@dataclass
class Candidate:
    """Una opción de descarga concreta, comparable entre fuentes distintas.

    `ref` es opaco y solo lo entiende la fuente que lo creó (una URL de YouTube,
    un identificador+fichero de archive.org…), de modo que añadir fuentes no
    obliga a tocar el orquestador.
    """

    source: str
    ref: str
    title: str
    quality: Quality
    artist: str | None = None
    album: str | None = None
    track_number: int | None = None
    date: str | None = None
    duration: float | None = None
    # Un ítem con varias pistas (un concierto, un álbum) en vez de una canción.
    is_album: bool = False
    track_count: int = 1
    # True si es la propia entrada que dio el usuario (no una alternativa).
    is_original_input: bool = False
    match_score: float = 1.0
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def rank(self) -> tuple:
        """Orden de preferencia: calidad real y, a igualdad, mejor coincidencia."""
        return (*self.quality.rank(), self.match_score)

    def describe(self) -> str:
        who = f"{self.artist} — " if self.artist else ""
        return f"[{self.source}] {who}{self.title} · {self.quality.label()}"
