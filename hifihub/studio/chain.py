"""Cadena de efectos offline con pedalboard.

Lista de specs (dicts) `{"type": ..., ...params}`. Se traduce a un `pedalboard.Pedalboard` y se aplica a una COPIA del audio.

  1. ffmpeg decodifica el original a WAV temporal (preserva sample rate) — así
     entran también Opus/M4A que pedalboard no lee nativamente.
  2. pedalboard aplica la cadena (EQ, dinámica, VST3 del usuario…).
  3. ffmpeg codifica el resultado a FLAC (lossless; con dither si baja a 16-bit).

El original nunca se abre en modo escritura.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hifihub import paths

# ¿Está pedalboard disponible?
try:
    import pedalboard  # noqa: F401
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


class StudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class EffectSpec:
    """Descriptor de un efecto en la interfaz (para poblar la UI)."""
    type: str
    label: str
    params: dict[str, Any]  # nombre -> {default, min, max, unit}


# Catálogo de efectos nativos disponibles (sin contar VST3 del usuario).
CATALOG: list[EffectSpec] = [
    EffectSpec("gain", "Ganancia", {"gain_db": {"default": 0, "min": -24, "max": 24, "unit": "dB"}}),
    EffectSpec("highpass", "Filtro paso-alto", {"cutoff_hz": {"default": 30, "min": 10, "max": 500, "unit": "Hz"}}),
    EffectSpec("lowpass", "Filtro paso-bajo", {"cutoff_hz": {"default": 20000, "min": 2000, "max": 22000, "unit": "Hz"}}),
    EffectSpec("lowshelf", "Shelf graves", {
        "cutoff_hz": {"default": 120, "min": 20, "max": 500, "unit": "Hz"},
        "gain_db": {"default": 0, "min": -18, "max": 18, "unit": "dB"},
        "q": {"default": 0.7, "min": 0.1, "max": 4, "unit": "Q"}}),
    EffectSpec("peak", "EQ paramétrico", {
        "cutoff_hz": {"default": 1000, "min": 20, "max": 20000, "unit": "Hz"},
        "gain_db": {"default": 0, "min": -18, "max": 18, "unit": "dB"},
        "q": {"default": 1.0, "min": 0.1, "max": 8, "unit": "Q"}}),
    EffectSpec("highshelf", "Shelf agudos", {
        "cutoff_hz": {"default": 8000, "min": 2000, "max": 20000, "unit": "Hz"},
        "gain_db": {"default": 0, "min": -18, "max": 18, "unit": "dB"},
        "q": {"default": 0.7, "min": 0.1, "max": 4, "unit": "Q"}}),
    EffectSpec("compressor", "Compresor", {
        "threshold_db": {"default": -18, "min": -60, "max": 0, "unit": "dB"},
        "ratio": {"default": 2, "min": 1, "max": 20, "unit": ":1"},
        "attack_ms": {"default": 5, "min": 0.1, "max": 100, "unit": "ms"},
        "release_ms": {"default": 100, "min": 10, "max": 1000, "unit": "ms"}}),
    EffectSpec("limiter", "Limitador", {
        "threshold_db": {"default": -1, "min": -12, "max": 0, "unit": "dB"},
        "release_ms": {"default": 100, "min": 10, "max": 1000, "unit": "ms"}}),
]


def _build_effect(spec: dict[str, Any]):
    import pedalboard as pb

    t = spec.get("type")
    p = spec
    if t == "gain":
        return pb.Gain(gain_db=p.get("gain_db", 0))
    if t == "highpass":
        return pb.HighpassFilter(cutoff_frequency_hz=p.get("cutoff_hz", 30))
    if t == "lowpass":
        return pb.LowpassFilter(cutoff_frequency_hz=p.get("cutoff_hz", 20000))
    if t == "lowshelf":
        return pb.LowShelfFilter(cutoff_frequency_hz=p.get("cutoff_hz", 120),
                                 gain_db=p.get("gain_db", 0), q=p.get("q", 0.7))
    if t == "highshelf":
        return pb.HighShelfFilter(cutoff_frequency_hz=p.get("cutoff_hz", 8000),
                                  gain_db=p.get("gain_db", 0), q=p.get("q", 0.7))
    if t == "peak":
        return pb.PeakFilter(cutoff_frequency_hz=p.get("cutoff_hz", 1000),
                             gain_db=p.get("gain_db", 0), q=p.get("q", 1.0))
    if t == "compressor":
        return pb.Compressor(threshold_db=p.get("threshold_db", -18), ratio=p.get("ratio", 2),
                             attack_ms=p.get("attack_ms", 5), release_ms=p.get("release_ms", 100))
    if t == "limiter":
        return pb.Limiter(threshold_db=p.get("threshold_db", -1), release_ms=p.get("release_ms", 100))
    if t == "vst3":
        return _load_vst3(p)
    raise StudioError(f"Efecto desconocido: {t!r}")


def _load_vst3(spec: dict[str, Any]):
    import pedalboard as pb

    path = spec.get("path")
    if not path or not Path(path).exists():
        raise StudioError(f"Plugin VST3 no encontrado: {path}")
    plugin = pb.load_plugin(path)
    for name, value in (spec.get("params") or {}).items():
        if hasattr(plugin, name):
            try:
                setattr(plugin, name, value)
            except Exception:  # noqa: BLE001 — parámetro incompatible: se ignora
                pass
    return plugin


def build_board(chain: list[dict[str, Any]]):
    import pedalboard as pb

    return pb.Pedalboard([_build_effect(s) for s in chain])


def process_file(
    src: Path | str,
    dst: Path | str,
    chain: list[dict[str, Any]],
    output_codec: str = "flac",
) -> Path:
    """Aplica la cadena a `src` y escribe el resultado en `dst` (nunca toca src)."""
    if not AVAILABLE:
        raise StudioError("pedalboard no está instalado (extra 'studio').")
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise StudioError(f"No existe: {src}")

    import pedalboard as pb
    from pedalboard.io import AudioFile

    tmp_in = _mkstemp_closed(".wav")
    tmp_out = _mkstemp_closed(".wav")
    try:
        # 1. Decodificar a WAV f32 (universal, preserva sample rate).
        _run_ffmpeg(["-i", str(src), "-c:a", "pcm_f32le", str(tmp_in)])

        # 2. Procesar con pedalboard.
        with AudioFile(str(tmp_in)) as f:
            audio = f.read(f.frames)
            sr, ch = f.samplerate, f.num_channels
        board = build_board(chain)
        processed = board(audio, sr)
        with AudioFile(str(tmp_out), "w", sr, ch) as f:
            f.write(processed)

        # 3. Codificar al formato final (FLAC lossless por defecto).
        dst.parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(["-i", str(tmp_out), "-c:a", output_codec, str(dst)])
        return dst
    finally:
        tmp_in.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)


def _mkstemp_closed(suffix: str) -> Path:
    """mkstemp cerrando el fd (Windows bloquea el archivo si queda abierto)."""
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return Path(name)


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [str(paths.ffmpeg_exe()), "-v", "error", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise StudioError(f"ffmpeg falló: {result.stderr.strip()}")


def default_output_path(src: Path | str, out_dir: Path | str | None = None) -> Path:
    """Ruta de la copia procesada: sufijo ' (procesado)' junto al original, o en
    `out_dir` si se indica. Siempre .flac (lossless)."""
    src = Path(src)
    stem = f"{src.stem} (procesado)"
    folder = Path(out_dir) if out_dir else src.parent
    return folder / f"{stem}.flac"


def copy_metadata(src: Path | str, dst: Path | str,
                  title_suffix: str = " (procesado)") -> None:
    """Copia los metadatos del original a la copia procesada.

    El re-codificado con ffmpeg deja el FLAC sin tags, así que aquí se heredan del
    original: título (con «(procesado)»), artista, álbum, album_artist, nº de
    pista, año, género, letra y portada. Se conserva el ÁLBUM y el nº de pista
    idénticos a propósito, para que la copia caiga en el mismo álbum y ordenada
    junto a la original. Tolerante a fallos: nunca debe tumbar el procesado.
    """
    from hifihub.metadata.tagger import (
        embed_cover, extract_cover, read_common_tags, write_tags,
    )

    src, dst = Path(src), Path(dst)
    try:
        tags = read_common_tags(src)
        base = tags.title or src.stem
        tags.title = f"{base}{title_suffix}"
        write_tags(dst, tags)
    except Exception:  # sin tags igual sirve; el usuario puede editar
        pass
    try:
        cover = extract_cover(src)
        if cover:
            embed_cover(dst, cover)
    except Exception:  # error
        pass
