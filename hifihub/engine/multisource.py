"""Esto ayuda a encontrar la misma grabación en varios orígenes y
elegir el de mayor calidad real de audio.

Busca detectar el ORIGINAL descargable de SoundCloud/Archive cuando el artista lo habilita
(a veces WAV/FLAC), elegir entre versiones de YouTube la de mejor stream real
(canal "Topic" vs. video recomprimido), o AAC 256 con Premium.

El matching es estricto (duración ±3 s + similitud de título) para no traer
covers, remixes ni versiones en vivo por error. Todo se decide sin descargar:
`extract_info(download=False)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

import yt_dlp

from hifihub import paths
from hifihub.metadata import cleaner

# Codecs sin pérdida que podrían aparecer (SoundCloud original, algún mirror).
LOSSLESS_CODECS = frozenset({"flac", "alac", "wav", "aiff", "pcm", "pcm_s16le", "pcm_s24le"})

# Eficiencia relativa por codec a igual bitrate (opus rinde más que aac que mp3).
CODEC_WEIGHT = {"opus": 1.30, "aac": 1.0, "vorbis": 1.05, "mp3": 0.90}

DURATION_TOLERANCE = 3.0   # segundos
SIMILARITY_THRESHOLD = 0.55


@dataclass(frozen=True)
class Quality:
    codec: str
    abr: float | None            # kbps
    asr: int | None              # Hz
    ext: str
    is_lossless: bool
    filesize: int | None = None

    @property
    def effective_kbps(self) -> float:
        """Bitrate ponderado por eficiencia del codec."""
        if self.is_lossless:
            return 1e6  # por encima de cualquier lossy
        if not self.abr:
            return 0.0
        return self.abr * CODEC_WEIGHT.get(self.codec, 1.0)

    def rank(self) -> tuple:
        return (int(self.is_lossless), round(self.effective_kbps, 1), self.asr or 0)

    def label(self) -> str:
        if self.is_lossless:
            base = f"{self.codec.upper()} lossless"
        else:
            base = f"{self.codec.upper()} ~{round(self.abr)} kbps" if self.abr else self.codec.upper()
        if self.asr:
            base += f" {self.asr / 1000:g} kHz"
        return base


@dataclass
class Candidate:
    source: str
    url: str
    title: str
    uploader: str | None
    duration: float | None
    quality: Quality
    is_original_input: bool = False
    match_score: float = 1.0
    notes: list[str] = field(default_factory=list)

    def rank(self) -> tuple:
        return self.quality.rank()


# --- Normalización y similitud (puro) ----------------------------------------

def normalize_codec(acodec: str | None) -> str:
    if not acodec:
        return "?"
    a = acodec.lower()
    if a.startswith("mp4a") or "aac" in a:
        return "aac"
    if a.startswith("opus"):
        return "opus"
    if a.startswith("mp3") or a == "mp3":
        return "mp3"
    for lossless in LOSSLESS_CODECS:
        if a.startswith(lossless):
            return "flac" if lossless in ("flac",) else lossless
    return a


def normalize_text(text: str) -> str:
    """Minúsculas, sin ruido de título, sin puntuación ni 'feat/ft'."""
    t = cleaner.clean_title(text or "").lower()
    t = re.sub(r"\bfe?a?t\.?\b.*$", "", t)      # quita "feat. X" / "ft X"
    t = re.sub(r"[^\w\s]", " ", t)               # puntuación -> espacio
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


# --- Extracción de calidad de un info dict de yt-dlp -------------------------

def best_audio_format(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Mejor formato SOLO-audio (o con audio) por calidad ponderada."""
    audio = [
        f for f in formats or []
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    # Si no hay solo-audio, aceptar formatos con audio incrustado.
    if not audio:
        audio = [f for f in formats or [] if f.get("acodec") not in (None, "none")]
    if not audio:
        return None
    return max(audio, key=lambda f: _format_quality(f).rank())


def _format_quality(fmt: dict[str, Any]) -> Quality:
    codec = normalize_codec(fmt.get("acodec"))
    return Quality(
        codec=codec,
        abr=fmt.get("abr") or fmt.get("tbr"),
        asr=fmt.get("asr"),
        ext=fmt.get("ext", "?"),
        is_lossless=codec in LOSSLESS_CODECS or (fmt.get("ext") in ("flac", "wav", "aiff")),
        filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
    )


def quality_from_info(info: dict[str, Any]) -> Quality:
    fmt = best_audio_format(info.get("formats", []))
    if fmt is None:
        # Info sin formatos.
        codec = normalize_codec(info.get("acodec"))
        return Quality(
            codec=codec, abr=info.get("abr"), asr=info.get("asr"),
            ext=info.get("ext", "?"), is_lossless=codec in LOSSLESS_CODECS,
        )
    return _format_quality(fmt)


# --- Matching ----------------------------------------------------------------

def matches(
    orig_duration: float | None,
    orig_title: str,
    cand_duration: float | None,
    cand_title: str,
    dur_tol: float = DURATION_TOLERANCE,
    sim_threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[bool, float]:
    """¿es la misma grabación?"""
    sim = title_similarity(orig_title, cand_title)
    if orig_duration and cand_duration and abs(orig_duration - cand_duration) > dur_tol:
        return False, sim
    return sim >= sim_threshold, sim


# --- Capa de red -------------------------------------------------------------

# Prefijos de búsqueda de yt-dlp por fuente.
SEARCH_PREFIXES = {
    "youtube": "ytsearch{n}:",
    "soundcloud": "scsearch{n}:",
}


def _base_opts(cookies_browser: str = "", cookies_file: str = "") -> dict[str, Any]:
    from hifihub.engine.downloader import JS_RUNTIMES

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "ffmpeg_location": str(paths.tools_dir()),
        "js_runtimes": JS_RUNTIMES,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def _extract(url: str, opts: dict[str, Any]) -> dict[str, Any] | None:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:  # fuente caída no debe abortar la búsqueda
        return None


def _search_source(source: str, query: str, n: int, opts: dict[str, Any]) -> list[dict[str, Any]]:
    prefix = SEARCH_PREFIXES.get(source)
    if not prefix:
        return []
    info = _extract(prefix.format(n=n) + query, opts)
    if not info:
        return []
    return [e for e in (info.get("entries") or []) if e]


def _candidate_from_entry(source: str, entry: dict[str, Any], is_input: bool = False) -> Candidate:
    return Candidate(
        source=source,
        url=entry.get("webpage_url") or entry.get("url") or entry.get("original_url", ""),
        title=entry.get("title", "?"),
        uploader=entry.get("uploader") or entry.get("channel"),
        duration=entry.get("duration"),
        quality=quality_from_info(entry),
        is_original_input=is_input,
    )


def find_best_source(
    url: str,
    *,
    sources: list[str] | None = None,
    per_source: int = 4,
    cookies_browser: str = "",
    cookies_file: str = "",
    progress: Any = None,
) -> list[Candidate]:
    """Devuelve candidatos que son la MISMA grabación que `url`, ordenados de
    mejor a peor calidad. El primero es la fuente recomendada. La entrada
    original siempre se incluye como candidato.
    """
    sources = sources or ["youtube", "soundcloud"]

    from hifihub.engine import session as session_mod

    with session_mod.session_cookiefile() as sess:
        if sess:
            cookies_file = sess
        return _search_all(
            url, sources, per_source, _base_opts(cookies_browser, cookies_file), progress
        )


def _search_all(url, sources, per_source, opts, progress) -> list[Candidate]:
    if progress:
        progress("Analizando el enlace original…")
    original = _extract(url, opts)
    if not original:
        return []
    orig_title = original.get("title", "")
    orig_dur = original.get("duration")
    query = _build_query(original)

    candidates: list[Candidate] = [_candidate_from_entry(
        _source_of(original), original, is_input=True)]

    for source in sources:
        if progress:
            progress(f"Buscando en {source}…")
        for entry in _search_source(source, query, per_source, opts):
            ok, score = matches(orig_dur, orig_title, entry.get("duration"), entry.get("title", ""))
            if not ok:
                continue
            cand = _candidate_from_entry(source, entry)
            cand.match_score = round(score, 3)
            # Evitar duplicar la propia entrada original.
            if cand.url and cand.url == candidates[0].url:
                continue
            candidates.append(cand)

    # Ordenar por calidad; a igualdad, priorizar mayor similitud de título.
    candidates.sort(key=lambda c: (c.rank(), c.match_score), reverse=True)

    original = next((c for c in candidates if c.is_original_input), None)
    if original is not None and candidates[0] is not original:
        if not _is_meaningfully_better(candidates[0].quality, original.quality):
            candidates.remove(original)
            candidates.insert(0, original)
            original.notes.append("Se conserva el original: sin mejora significativa.")
    return candidates


def _is_meaningfully_better(cand: Quality, original: Quality) -> bool:
    """Solo merece cambiar de fuente si hay un salto de calidad real."""
    if cand.is_lossless and not original.is_lossless:
        return True
    if original.is_lossless:
        return False
    # ≥15% más de bitrate efectivo (evita cambios por diferencias irrelevantes).
    return cand.effective_kbps >= original.effective_kbps * 1.15


def _build_query(info: dict[str, Any]) -> str:
    parsed = cleaner.parse(
        info.get("title", ""), uploader=info.get("uploader"), artist=info.get("artist")
    )
    parts = [parsed.artist, parsed.title] if parsed.artist else [parsed.title]
    return " ".join(p for p in parts if p)


def _source_of(info: dict[str, Any]) -> str:
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    if "soundcloud" in extractor:
        return "soundcloud"
    if "youtube" in extractor:
        return "youtube"
    return extractor or "origen"
