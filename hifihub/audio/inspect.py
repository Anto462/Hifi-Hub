"""Sondeo de la calidad REAL de un archivo de audio con ffprobe.

La detección espectral de "falso hi-res" (un archivo hi-res que en realidad
provino de un MP3) llegará en una fase posterior con análisis de frecuencias;
aquí se reporta lo que los metadatos del stream declaran.

Esto busca destacar aquellos archivos que indicar ser lossless sin serlo realmente.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hifihub import paths

LOSSLESS_CODECS = frozenset({
    "flac", "alac", "wav", "pcm_s16le", "pcm_s24le", "pcm_s32le", "aiff", "ape", "wavpack",
})


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    codec: str
    container: str
    sample_rate: int
    channels: int
    duration: float
    lossless: bool
    bit_rate: int | None = None      # bits/segundo; None si no se conoce
    bit_depth: int | None = None     # 16/24...; None si no aplica/desconoce

    @property
    def bit_rate_kbps(self) -> int | None:
        return round(self.bit_rate / 1000) if self.bit_rate else None

    @property
    def sample_rate_khz(self) -> float:
        return round(self.sample_rate / 1000, 1)

    def quality_label(self) -> str:
        """Etiqueta corta para UI/CLI, ej. 'Opus 48 kHz ~160 kbps'."""
        parts = [self.codec.upper(), f"{self.sample_rate_khz} kHz"]
        if self.bit_depth:
            parts.append(f"{self.bit_depth}-bit")
        if self.lossless:
            parts.append("lossless")
        elif self.bit_rate_kbps:
            parts.append(f"~{self.bit_rate_kbps} kbps")
        return " ".join(parts)


class ProbeError(RuntimeError):
    pass


def probe(path: Path | str) -> AudioInfo:
    path = Path(path)
    if not path.exists():
        raise ProbeError(f"No existe el archivo: {path}")

    cmd = [
        str(paths.ffprobe_exe()),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-select_streams", "a:0",
        str(path),
    ]
    # ffprobe emite JSON UTF-8; forzar la decodificación (en Windows el default
    # cp1252 revienta con tags o rutas no-ASCII, habituales en música).
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise ProbeError(f"ffprobe falló: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise ProbeError(f"Sin stream de audio en: {path}")
    st = streams[0]
    fmt = data.get("format", {})

    codec = st.get("codec_name", "?")
    bit_rate = _as_int(st.get("bit_rate")) or _as_int(fmt.get("bit_rate"))
    bit_depth = _as_int(st.get("bits_per_raw_sample")) or _as_int(st.get("bits_per_sample"))

    return AudioInfo(
        path=path,
        codec=codec,
        container=path.suffix.lstrip(".").lower(),
        sample_rate=_as_int(st.get("sample_rate")) or 0,
        channels=_as_int(st.get("channels")) or 0,
        duration=float(fmt.get("duration") or st.get("duration") or 0.0),
        lossless=codec in LOSSLESS_CODECS,
        bit_rate=bit_rate,
        bit_depth=bit_depth or None,
    )


def _as_int(value: object) -> int | None:
    try:
        n = int(value)  # type: ignore[arg-type]
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None
