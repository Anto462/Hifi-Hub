"""Eliminar físicamente los tramos no musicales.

Usa la base de datos comunitaria de SponsorBlock. La categoría estrella para
música es `music_offtopic` (secciones no musicales de videos musicales: intros
actuadas, diálogos, outros).

"""

from __future__ import annotations

from typing import Any

# Categorías de SponsorBlock relevantes para audio.
DEFAULT_CATEGORIES = ("music_offtopic",)
ALL_CATEGORIES = (
    "music_offtopic",  # tramo no musical de un video musical
    "sponsor",         # publicidad integrada
    "intro",           # cortinillas de entrada
    "outro",           # cierres/creditos
    "selfpromo",       # autopromoción
    "preview",         # resumen/avance
    "interaction",     # "dale like y suscríbete"
    "filler",          # relleno
)


def build_postprocessors(categories: tuple[str, ...] | list[str] = DEFAULT_CATEGORIES) -> list[dict[str, Any]]:
    """Postprocesadores: SponsorBlock marca los tramos consultando la
    API; ModifyChapters los corta físicamente. Deben ir ANTES del ExtractAudio
    en la lista para operar sobre la línea de tiempo original."""
    cats = [c for c in categories if c in ALL_CATEGORIES]
    if not cats:
        cats = list(DEFAULT_CATEGORIES)
    return [
        {"key": "SponsorBlock", "categories": cats, "when": "after_filter"},
        {"key": "ModifyChapters", "remove_sponsor_segments": cats},
    ]
