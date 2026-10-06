"""Corrección de auriculares por AutoEq (ParametricEQ) → filtros de ffmpeg/mpv.

Corrige la respuesta en frecuencia CONOCIDA de un auricular concreto hacia el
target Harman.

Formato AutoEq ParametricEQ.txt:
    Preamp: -6.3 dB
    Filter 1: ON LSC Fc 105 Hz Gain 7.6 dB Q 0.70   (low shelf)
    Filter 2: ON PK  Fc 113 Hz Gain -4.9 dB Q 0.29  (peaking)
    Filter 6: ON HSC Fc 10000 Hz Gain -0.2 dB Q 0.70 (high shelf)

Traducción a ffmpeg: PK->equalizer, LSC->bass (low-shelf), HSC->treble (high-shelf),
Preamp->volume. Se encadenan en un único graph lavfi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hifihub import paths

BUNDLED_DIR = Path(__file__).resolve().parent.parent / "data" / "autoeq"
USER_DIR = paths.CONFIG_DIR / "autoeq"

_FILTER_RE = re.compile(
    r"Filter\s+\d+:\s+ON\s+(\w+)\s+Fc\s+([\d.]+)\s*Hz\s+Gain\s+(-?[\d.]+)\s*dB\s+Q\s+([\d.]+)",
    re.IGNORECASE,
)
_PREAMP_RE = re.compile(r"Preamp:\s*(-?[\d.]+)\s*dB", re.IGNORECASE)


@dataclass(frozen=True)
class BiquadFilter:
    kind: str   # "PK" | "LSC" | "HSC"
    fc: float
    gain: float
    q: float


@dataclass(frozen=True)
class Correction:
    name: str
    preamp: float
    filters: list[BiquadFilter]


class HeadphoneError(RuntimeError):
    pass


def parse_parametric_eq(text: str, name: str = "?") -> Correction:
    preamp_match = _PREAMP_RE.search(text)
    preamp = float(preamp_match.group(1)) if preamp_match else 0.0
    filters: list[BiquadFilter] = []
    for kind, fc, gain, q in _FILTER_RE.findall(text):
        filters.append(BiquadFilter(kind.upper(), float(fc), float(gain), float(q)))
    if not filters:
        raise HeadphoneError("El archivo no contiene filtros ParametricEQ válidos")
    return Correction(name=name, preamp=preamp, filters=filters)


def _fmt(x: float) -> str:
    return f"{x:g}"


def correction_to_fragment(corr: Correction) -> str:
    """Cadena de filtros ffmpeg para esta corrección."""
    parts = [f"volume={_fmt(corr.preamp)}dB"] if corr.preamp else []
    for f in corr.filters:
        if f.gain == 0:
            continue
        if f.kind == "PK":
            parts.append(f"equalizer=f={_fmt(f.fc)}:t=q:w={_fmt(f.q)}:g={_fmt(f.gain)}")
        elif f.kind == "LSC":
            parts.append(f"bass=f={_fmt(f.fc)}:t=q:w={_fmt(f.q)}:g={_fmt(f.gain)}")
        elif f.kind == "HSC":
            parts.append(f"treble=f={_fmt(f.fc)}:t=q:w={_fmt(f.q)}:g={_fmt(f.gain)}")
    return ",".join(parts)


# --- Gestión de presets ------------------------------------------------------

def _display_name(path: Path) -> str:
    return path.stem.replace("_", " ")


def list_presets() -> list[dict[str, str]]:
    """Presets disponibles. Bundled + importados por el usuario."""
    seen: dict[str, dict[str, str]] = {}
    for directory, source in ((BUNDLED_DIR, "incluido"), (USER_DIR, "importado")):
        if not directory.exists():
            continue
        for txt in sorted(directory.glob("*.txt")):
            name = _display_name(txt)
            seen[name] = {"name": name, "file": str(txt), "source": source}
    return list(seen.values())


def load_correction(name: str) -> Correction:
    for directory in (USER_DIR, BUNDLED_DIR):  # el importado tiene prioridad
        txt = directory / f"{name.replace(' ', '_')}.txt"
        if txt.exists():
            return parse_parametric_eq(txt.read_text(encoding="utf-8"), name)
    # Búsqueda laxa por display name.
    for p in list_presets():
        if p["name"] == name:
            return parse_parametric_eq(Path(p["file"]).read_text(encoding="utf-8"), name)
    raise HeadphoneError(f"Preset no encontrado: {name!r}")


def import_preset(src_path: Path | str) -> str:
    """Copia un ParametricEQ.txt del usuario a la carpeta de presets. Valida el
    formato antes de guardar. Devuelve el nombre para seleccionarlo."""
    src = Path(src_path)
    text = src.read_text(encoding="utf-8", errors="replace")
    name = src.stem.replace(" ParametricEQ", "").strip()
    parse_parametric_eq(text, name)  # valida (lanza si no es AutoEq)
    USER_DIR.mkdir(parents=True, exist_ok=True)
    dest = USER_DIR / f"{name.replace(' ', '_')}.txt"
    dest.write_text(text, encoding="utf-8")
    return _display_name(dest)


def af_fragment_for(name: str | None) -> str:
    """Fragmento de filtros para el preset dado, o "" si no hay/está vacío."""
    if not name:
        return ""
    try:
        return correction_to_fragment(load_correction(name))
    except HeadphoneError:
        return ""
