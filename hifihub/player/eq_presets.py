"""Presets del ecualizador: guardar, cargar, exportar e importar curvas.

Cada preset es un JSON con las ganancias y las frecuencias centrales, así que un
preset creado con frecuencias personalizadas se restaura exactamente igual. Estos viven
en la carpeta de configuración del usuario.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hifihub import paths
from hifihub.player.dsp import EQ_FREQS

PRESETS_DIR = paths.CONFIG_DIR / "eq_presets"
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class PresetError(RuntimeError):
    pass


# --- Presets de fábrica -------------------------------------------------------

FACTORY_PRESETS: list[dict[str, Any]] = [
    {"name": "Neutro (referencia)",
     "gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     "why": "Sin coloración. El punto de partida honesto y la referencia para comparar."},
    {"name": "Rap / Hip-Hop",
     "gains": [3, 2.5, 0, -1.5, -0.5, 0, 1, 1.5, 0.5, 0],
     "why": "Peso del 808 en el sub; se quita barro en 250 Hz; presencia para que la voz rapeada se entienda sobre el beat."},
    {"name": "Rock / J-Rock",
     "gains": [1, 1, 0, -1, 0, 0.5, 1, -1.5, 1, 1],
     "why": "Cuerpo de guitarra y se doma 4 kHz, donde los masters muy comprimidos se vuelven ásperos."},
    {"name": "Clásica / Orquestal",
     "gains": [0, 0.5, 0.5, 0, 0, 0, 0, 0, 0.5, 1.5],
     "why": "Casi plana a propósito: es el género mejor grabado. Solo un toque de aire de sala."},
    {"name": "Cinemático / Bandas sonoras",
     "gains": [1.5, 1, 0, -0.5, 0, 0, 0.5, 0.5, 1, 2],
     "why": "Híbrido orquestal y sintético: algo de peso e impacto abajo, aire arriba, medios intactos."},
    {"name": "Electrónica",
     "gains": [3, 2, 0, -1, -0.5, 0, 0, 1, 1.5, 2],
     "why": "Enfasis en los sonidos de la track."},
    {"name": "Jazz / Acústico",
     "gains": [0, 1, 1, 0, 0, 0, 0.5, 1, 1, 0.5],
     "why": "Calidez del contrabajo y cuerpo del saxo; agudos naturales, sin filo."},
    {"name": "Voz / Podcast",
     "gains": [-3, -2, -1, -1.5, 0, 1, 2, 2, 1, 0],
     "why": "Inteligibilidad: fuera retumbe, dentro presencia."},
    {"name": "Escucha a bajo volumen",
     "gains": [4, 3, 1.5, 0, 0, 0, 0, 0.5, 2, 3],
     "why": "Compensación de sonoridad, a volumen bajo el oído pierde graves y agudos."},
]

_FACTORY_BY_NAME = {p["name"]: p for p in FACTORY_PRESETS}


def list_factory() -> list[dict[str, Any]]:
    """Presets de fábrica (no se pueden borrar ni sobrescribir)."""
    return [
        {"name": p["name"], "gains": list(p["gains"]),
         "freqs": [float(f) for f in EQ_FREQS], "why": p["why"], "factory": True}
        for p in FACTORY_PRESETS
    ]


def load_factory(name: str) -> dict[str, Any]:
    p = _FACTORY_BY_NAME.get(name)
    if p is None:
        raise PresetError(f"No existe el preset de fábrica «{name}».")
    # Los de fábrica siempre usan las bandas ISO: son curvas de referencia.
    return {"name": p["name"], "gains": list(p["gains"]),
            "freqs": [float(f) for f in EQ_FREQS], "why": p["why"]}


def _safe_name(name: str) -> str:
    name = _INVALID.sub("_", (name or "").strip()).strip(". ")
    if not name:
        raise PresetError("Ponle un nombre al preset.")
    return name[:60]


def _path_for(name: str) -> Path:
    return PRESETS_DIR / f"{_safe_name(name)}.json"


def save_preset(name: str, gains: list[float], freqs: list[float] | None = None) -> str:
    safe = _safe_name(name)
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": safe,
        "gains": [round(float(g), 2) for g in gains],
        "freqs": [round(float(f), 2) for f in (freqs or EQ_FREQS)],
    }
    _path_for(safe).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return safe


def load_preset(name: str) -> dict[str, Any]:
    path = _path_for(name)
    if not path.exists():
        raise PresetError(f"No existe el preset «{name}».")
    return parse_preset(path.read_text(encoding="utf-8"))


def parse_preset(text: str) -> dict[str, Any]:
    """Valida el contenido de un preset (también sirve para importar archivos)."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise PresetError(f"El archivo no es un preset válido: {e}") from e
    gains = data.get("gains")
    if not isinstance(gains, list) or len(gains) != len(EQ_FREQS):
        raise PresetError(f"Un preset debe traer {len(EQ_FREQS)} ganancias.")
    try:
        gains = [float(g) for g in gains]
    except (TypeError, ValueError) as e:
        raise PresetError("Las ganancias deben ser números.") from e
    freqs = data.get("freqs")
    if isinstance(freqs, list) and len(freqs) == len(EQ_FREQS):
        try:
            freqs = [float(f) for f in freqs]
        except (TypeError, ValueError):
            freqs = None
    else:
        freqs = None
    return {"name": str(data.get("name") or "preset"), "gains": gains, "freqs": freqs}


def list_presets() -> list[dict[str, Any]]:
    if not PRESETS_DIR.is_dir():
        return []
    out = []
    for p in sorted(PRESETS_DIR.glob("*.json")):
        try:
            data = parse_preset(p.read_text(encoding="utf-8"))
        except PresetError:
            continue          # un archivo corrupto no invalida la lista
        out.append({"name": p.stem, "gains": data["gains"], "freqs": data["freqs"]})
    return out


def delete_preset(name: str) -> None:
    _path_for(name).unlink(missing_ok=True)
