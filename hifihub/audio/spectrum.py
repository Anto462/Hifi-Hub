"""Análisis espectral: espectrograma visual y funciona como detector de "falso hi-res".

¿Este archivo proviene realmente de una fuente lossless, o alguien re-subió un MP3 recomprimido?
Los codecs lossy recortan las frecuencias altas (un MP3 de 128k corta ~16 kHz);
si un FLAC de 44.1 kHz no tiene energía por encima de 16 kHz, la fuente fue lossy.

Igual no es una ley de oro pero para grabaciones modernas suele ser el caso. No es tan viable en grabaciones mas antiguas.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from hifihub import paths
from hifihub.audio import inspect as audio_inspect

_NFFT = 8192
_ANALYZE_SECONDS = 60  # analizar hasta 60s del centro es suficiente y rápido
_DROP_DB = 35.0        # caída respecto a la banda media que define el "corte"


class SpectrumError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpectralAnalysis:
    cutoff_hz: float          # frecuencia efectiva más alta con contenido
    nyquist_hz: float         # máximo teórico del sample rate
    ratio: float              # cutoff / nyquist
    suspicious: bool          # True si huele a fuente lossy re-empaquetada
    verdict: str              # texto returned para UI/CLI

    @property
    def cutoff_khz(self) -> float:
        return round(self.cutoff_hz / 1000, 1)


def _decode_mono(path: Path, seconds: int = _ANALYZE_SECONDS) -> tuple[np.ndarray, int]:
    info = audio_inspect.probe(path)
    sr = info.sample_rate or 44100
    # Saltar la intro (poca energía) y decodificar el tramo central.
    start = max(0.0, (info.duration - seconds) / 2) if info.duration else 0.0
    cmd = [
        str(paths.ffmpeg_exe()), "-v", "error",
        "-ss", str(start), "-t", str(seconds),
        "-i", str(path),
        "-ac", "1", "-f", "f32le", "-c:a", "pcm_f32le", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise SpectrumError(f"No se pudo decodificar {path}")
    return np.frombuffer(result.stdout, dtype=np.float32), sr


def _average_spectrum_db(samples: np.ndarray) -> np.ndarray:
    """Espectro medio (método de Welch simplificado): ventanas Hann + promedio."""
    n_frames = max(1, len(samples) // _NFFT)
    window = np.hanning(_NFFT)
    acc = np.zeros(_NFFT // 2 + 1)
    for i in range(n_frames):
        frame = samples[i * _NFFT:(i + 1) * _NFFT]
        if len(frame) < _NFFT:
            break
        acc += np.abs(np.fft.rfft(frame * window)) ** 2
    acc /= max(1, n_frames)
    return 10 * np.log10(acc + 1e-12)


def analyze(path: Path | str) -> SpectralAnalysis:
    path = Path(path)
    samples, sr = _decode_mono(path)
    if len(samples) < _NFFT:
        raise SpectrumError("Archivo demasiado corto para analizar")

    spectrum_db = _average_spectrum_db(samples)
    freqs = np.fft.rfftfreq(_NFFT, d=1.0 / sr)
    nyquist = sr / 2

    # Ref - nivel mediano en la banda media (1-6 kHz), donde vive la música.
    mid = spectrum_db[(freqs >= 1000) & (freqs <= 6000)]
    if mid.size == 0:
        raise SpectrumError("Sample rate demasiado bajo para el análisis")
    reference = float(np.median(mid))

    # Corte efectivo - la frecuencia más alta cuyo nivel sigue a menos de
    # _DROP_DB de la referencia.
    alive = (spectrum_db > reference - _DROP_DB) & (freqs > 0)
    cutoff = float(freqs[alive].max()) if alive.any() else 0.0
    ratio = cutoff / nyquist

    suspicious = ratio < 0.75
    if not suspicious:
        verdict = "Espectro completo: coherente con la fuente declarada."
    elif cutoff < 17000:
        verdict = (f"Corte a ~{cutoff / 1000:.1f} kHz: huele a fuente lossy de bitrate "
                   "bajo (≈MP3/AAC 128k) re-empaquetada.")
    elif cutoff < 20000:
        verdict = (f"Corte a ~{cutoff / 1000:.1f} kHz: probable fuente lossy "
                   "(≈192-256 kbps) re-empaquetada.")
    else:
        verdict = (f"Contenido hasta ~{cutoff / 1000:.1f} kHz con Nyquist a "
                   f"{nyquist / 1000:g} kHz: posible upsampling de una fuente inferior.")
    return SpectralAnalysis(
        cutoff_hz=cutoff, nyquist_hz=nyquist, ratio=round(ratio, 3),
        suspicious=suspicious, verdict=verdict,
    )


def spectrogram_png(path: Path | str, size: str = "1024x512") -> bytes:
    """Espectrograma como PNG (ffmpeg showspectrumpic) para inspección visual."""
    cmd = [
        str(paths.ffmpeg_exe()), "-v", "error",
        "-i", str(path),
        "-lavfi", f"showspectrumpic=s={size}:legend=1",
        "-frames:v", "1", "-f", "image2", "-c:v", "png", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise SpectrumError(f"No se pudo generar el espectrograma: {result.stderr.decode(errors='replace')[:200]}")
    return result.stdout
