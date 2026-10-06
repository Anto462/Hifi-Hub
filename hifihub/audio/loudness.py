"""Normalización de volumen EBU R128 en dos modos.

1. `scan_files` (DEFAULT, no destructivo): rsgain escribe tags ReplayGain 2.0
   (REPLAYGAIN_TRACK_GAIN / _ALBUM_GAIN). El audio no se toca; el reproductor
   (nuestro libmpv, Foobar2000, etc.) aplica la ganancia al reproducir. Modo
   álbum disponible para preservar la dinámica relativa entre pistas.

2. `loudnorm_two_pass` (OPT-IN, destructivo): "quema" el filtro loudnorm de
   ffmpeg en el archivo. Recodifica, así que degrada fuentes lossy; solo tiene sentido para equipos que ignoran ReplayGain. En todo caso no recomiendo usarlo pero bueno mejor que sobre a que falte.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hifihub import paths
from hifihub.audio import inspect as audio_inspect


class LoudnessError(RuntimeError):
    pass


@dataclass(frozen=True)
class GainResult:
    path: str
    loudness_lufs: float | None
    gain_db: float | None


# --- Modo 1: ReplayGain con rsgain (no destructivo) --------------------------

def scan_files(
    files: list[Path] | list[str],
    album: bool = False,
    target_lufs: float | None = None,
) -> list[GainResult]:
    """Escribe tags ReplayGain 2.0 en `files`. `album=True` añade ganancia de
    álbum calculada sobre el conjunto (para playlists/álbumes completos)."""
    files = [str(f) for f in files]
    if not files:
        return []

    cmd = [str(paths.rsgain_exe()), "custom", "-s", "i", "-O"]
    if album:
        cmd.append("-a")
    if target_lufs is not None:
        cmd += ["-l", str(target_lufs)]
    cmd += files

    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise LoudnessError(f"rsgain falló: {result.stderr.strip() or result.stdout.strip()}")
    return _parse_rsgain_output(result.stdout)


def _parse_rsgain_output(stdout: str) -> list[GainResult]:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")

    def col(row: list[str], name: str) -> str | None:
        try:
            return row[header.index(name)]
        except (ValueError, IndexError):
            return None

    out: list[GainResult] = []
    for ln in lines[1:]:
        row = ln.split("\t")
        out.append(GainResult(
            path=col(row, "Filename") or row[0],
            loudness_lufs=_to_float(col(row, "Loudness (LUFS)")),
            gain_db=_to_float(col(row, "Gain (dB)")),
        ))
    return out


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# --- Modo 2: loudnorm two-pass (destructivo, opt-in) --------------------------

# Diccionario de encoders de salida por codec de origen (para re-encodear al mismo formato).
_ENCODERS: dict[str, list[str]] = {
    "flac": ["-c:a", "flac"],
    "opus": ["-c:a", "libopus", "-b:a", "160k"],
    "mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    "aac": ["-c:a", "aac", "-b:a", "256k"],
    "alac": ["-c:a", "alac"],
    "vorbis": ["-c:a", "libvorbis", "-q:a", "7"],
}


def loudnorm_two_pass(
    path: Path | str,
    target_lufs: float = -14.0,
    true_peak: float = -1.0,
    lra: float = 11.0,
) -> dict:
    """Aplica loudnorm EBU R128 en dos pasadas, reemplazando el archivo.

    Devuelve las medidas de la primera pasada. DESTRUCTIVO: recodifica.
    """
    path = Path(path)
    info = audio_inspect.probe(path)
    encoder = _ENCODERS.get(info.codec)
    if encoder is None:
        raise LoudnessError(f"loudnorm no soporta el codec {info.codec!r}")

    base = f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}"

    # 1-medir.
    measured = _measure(path, base)

    # 2-aplicar con los valores medidos (modo lineal si es posible).
    filt = (
        f"{base}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )
    # mkstemp devuelve un fd abierto: cerrarlo ya, o Windows bloquea el replace.
    fd, tmp_name = tempfile.mkstemp(suffix=path.suffix, dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    cmd = [
        str(paths.ffmpeg_exe()), "-v", "error", "-y", "-i", str(path),
        "-af", filt,
        # loudnorm trabaja internamente a 192 kHz: volver al sample rate original.
        "-ar", str(info.sample_rate),
        *encoder, str(tmp),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise LoudnessError(f"loudnorm (pasada 2) falló: {result.stderr.strip()}")
    tmp.replace(path)
    return measured


def _measure(path: Path, base_filter: str) -> dict:
    cmd = [
        str(paths.ffmpeg_exe()), "-hide_banner", "-nostats", "-y",
        "-i", str(path),
        "-af", f"{base_filter}:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise LoudnessError(f"loudnorm (pasada 1) falló: {result.stderr.strip()}")
    # El JSON de loudnorm es el último bloque {...} del stderr.
    blocks = re.findall(r"\{[^{}]*\}", result.stderr)
    if not blocks:
        raise LoudnessError("loudnorm no emitió medidas JSON")
    return json.loads(blocks[-1])
