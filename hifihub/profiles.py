"""Perfiles de calidad de audio.

Cada perfil se traduce a las opciones que consume yt-dlp/ffmpeg.

- "purista" (default) NO recodifica: descarga el stream nativo del servidor
  (Opus/M4A) y solo lo re-muxea a su contenedor propio. Bit-perfect.
- "flac"/"alac" son lossless: útiles por compatibilidad de contenedor, no
  mejoran un origen lossy. Solo resamplean si el usuario lo pide, y entonces
  usan el motor soxr (superior al nativo de ffmpeg) + dithering triangular al
  bajar profundidad de bits.
- "mp3" es lossy: solo por compatibilidad con equipos que no leen otra cosa.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class AudioProfile:
    name: str
    # preferredcodec de yt-dlp: "best" (sin recodificar), "flac", "alac", "mp3"
    codec: str
    lossless: bool
    description: str
    # Solo aplica a codecs con recodificación (flac/alac). None = conservar origen.
    target_sample_rate: int | None = None
    target_bit_depth: int | None = None  # 16 o 24; None = conservar
    dither: str = "triangular"
    mp3_quality: str = "320"

    @property
    def recodes(self) -> bool:
       # True si el perfil transforma el audio (todo salvo purista).
        return self.codec != "best"

    def with_resample(
        self,
        sample_rate: int | None = None,
        bit_depth: int | None = None,
    ) -> "AudioProfile":
        # Devuelve una copia con resampleo/profundidad (para CLI/UI).
        return replace(
            self,
            target_sample_rate=sample_rate if sample_rate is not None else self.target_sample_rate,
            target_bit_depth=bit_depth if bit_depth is not None else self.target_bit_depth,
        )


PRESETS: dict[str, AudioProfile] = {
    "purista": AudioProfile(
        name="purista",
        codec="best",
        lossless=True,  # preserva el original tal cual lo entrega el servidor
        description="Stream nativo (Opus/M4A) sin recodificar. Máxima fidelidad.",
    ),
    "flac": AudioProfile(
        name="flac",
        codec="flac",
        lossless=True,
        description="FLAC lossless. Compatibilidad con gestores tipo Roon/Foobar2000.",
    ),
    "alac": AudioProfile(
        name="alac",
        codec="alac",
        lossless=True,
        description="ALAC lossless (contenedor M4A). Ecosistema Apple.",
    ),
    "mp3": AudioProfile(
        name="mp3",
        codec="mp3",
        lossless=False,
        description="MP3 320 kbps. Con pérdida; solo por compatibilidad.",
    ),
}

DEFAULT_PROFILE = "purista"


def get_profile(name: str) -> AudioProfile:
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(
            f"Perfil desconocido: {name!r}. Opciones: {', '.join(PRESETS)}"
        ) from None


def _aresample_filter(profile: AudioProfile) -> str | None:
    """Construye un único filtro `aresample` con soxr y dithering.

    Todo va en un solo filtro a propósito: usar `-ar` por separado haría que
    ffmpeg insertara un resampler nativo adicional, ignorando soxr (gotcha
    clásico). El dithering se aplica al reducir la profundidad de bits.
    """
    opts: list[str] = []
    if profile.target_sample_rate:
        opts.append("resampler=soxr")
        opts.append("precision=28")  # soxr VHQ
        opts.append(f"osr={profile.target_sample_rate}")
    if profile.target_bit_depth == 16:
        opts.append(f"dither_method={profile.dither}")
    return "aresample=" + ":".join(opts) if opts else None


def ffmpeg_postprocessor_args(profile: AudioProfile) -> list[str]:
    """Args extra para la llamada de ffmpeg del FFmpegExtractAudio."""
    if not profile.recodes:
        return []
    args: list[str] = []
    afilter = _aresample_filter(profile)
    if afilter:
        args += ["-af", afilter]
    if profile.target_bit_depth == 16:
        args += ["-sample_fmt", "s16"]
    elif profile.target_bit_depth == 24:
        args += ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"]
    return args


def build_postprocessors(profile: AudioProfile) -> list[dict[str, Any]]:
    pp: dict[str, Any] = {
        "key": "FFmpegExtractAudio",
        "preferredcodec": profile.codec,
    }
    if profile.codec == "mp3":
        pp["preferredquality"] = profile.mp3_quality
    return [pp]
