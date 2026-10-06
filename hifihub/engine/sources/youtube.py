"""Fuente: YouTube (vía yt-dlp).

Aquí vive TODO lo específico de YouTube, que es la parte más frágil del sistema
porque YouTube cambia su player a menudo:

- `player_client`: con cuenta autenticada, el cliente `web` exige un "PO Token"
  que yt-dlp no genera, y entonces no encuentra formatos. Estos clientes sirven
  el audio sin ese requisito.
- Reintento sin autenticación: por el mismo motivo, autenticar suele EMPEORAR el
  resultado en YouTube. La política está documentada en `politica_autenticacion()`.
- Runtimes JS: sin node/deno, YouTube limita formatos y dispara antes el bloqueo
  por bot.

`downloader.py` es el motor genérico de yt-dlp; este módulo es la política de
YouTube. Si YouTube se rompe, el fallo queda contenido aquí y en yt-dlp: las
fuentes que no usan yt-dlp (archive.org) siguen funcionando.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from hifihub.engine.sources.base import Candidate, SourceError

NAME = "youtube"
LABEL = "YouTube"

# Clientes de reproductor a usar cuando hay CUENTA AUTENTICADA (sesión/cookies).
# Ver `politica_autenticacion()`.
DEFAULT_PLAYER_CLIENTS = "web_safari,mweb,ios,web_music"

# Runtimes JS que yt-dlp puede usar para resolver los retos de YouTube.
JS_RUNTIMES = {"deno": {}, "node": {}}


def politica_autenticacion() -> str:
    """Por qué NO usamos cookies con YouTube (verificado con cookies reales).

    Autenticar contra YouTube exige hoy un PO Token que yt-dlp no puede generar:
    con cookies el extractor devuelve cero formatos ("No video formats found") o
    "Requested format is not available", y los clientes móviles se omiten. Por eso
    la UI no ofrece cookies y el motor reintenta SIN autenticar ante cualquier
    DownloadError: un vídeo realmente inaccesible falla igual y se propaga su
    error; si la culpa era la autenticación, entonces sí descarga.
    """
    return politica_autenticacion.__doc__ or ""


def set_player_clients(opts: dict[str, Any], clients_csv: str) -> None:
    """Fija `extractor_args.youtube.player_client` en unas opciones de yt-dlp."""
    clients = [c.strip() for c in (clients_csv or "").split(",") if c.strip()]
    if not clients:
        return
    ea = opts.setdefault("extractor_args", {})
    yt = ea.setdefault("youtube", {})
    yt["player_client"] = clients


def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in ("youtube.com/", "youtu.be/", "music.youtube.com/"))


# --- Búsqueda / inspección ---------------------------------------------------

def _base_opts() -> dict[str, Any]:
    from hifihub import paths

    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "ffmpeg_location": str(paths.tools_dir()),
        "js_runtimes": JS_RUNTIMES,
    }


def _extract(url: str) -> dict[str, Any] | None:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:  # noqa: BLE001 — el fallo de una fuente no aborta la búsqueda
        return None


def _candidate(entry: dict[str, Any], *, is_input: bool = False,
               match_score: float = 1.0) -> Candidate:
    from hifihub.engine.multisource import quality_from_info

    return Candidate(
        source=NAME,
        ref=entry.get("webpage_url") or entry.get("url") or entry.get("original_url", ""),
        title=entry.get("title", "?"),
        quality=quality_from_info(entry),
        artist=entry.get("artist") or entry.get("uploader") or entry.get("channel"),
        album=entry.get("album"),
        date=str(entry.get("release_year") or "") or None,
        duration=entry.get("duration"),
        is_original_input=is_input,
        match_score=match_score,
    )


def candidate_for_url(url: str) -> Candidate | None:
    """Inspecciona un enlace de YouTube sin descargar nada."""
    info = _extract(url)
    return _candidate(info, is_input=True) if info else None


def search(query: str, limit: int = 4,
           progress: Callable[[str], None] | None = None) -> list[Candidate]:
    """Busca en YouTube y devuelve candidatos comparables."""
    if not (query or "").strip():
        return []
    if progress:
        progress("YouTube: buscando…")
    info = _extract(f"ytsearch{max(1, int(limit))}:{query}")
    if not info:
        return []
    return [_candidate(e) for e in (info.get("entries") or []) if e]


def find_track(title: str, artist: str = "", duration: float | None = None, *,
               limit: int = 4,
               progress: Callable[[str], None] | None = None) -> list[Candidate]:
    """Busca una canción concreta y filtra por duración + similitud de título."""
    from hifihub.engine.sources.base import matches

    query = " ".join(p for p in (artist, title) if p).strip()
    out: list[Candidate] = []
    for cand in search(query, limit=limit, progress=progress):
        ok, score = matches(duration, title, cand.duration, cand.title)
        if not ok:
            continue
        cand.match_score = round(score, 3)
        out.append(cand)
    out.sort(key=lambda c: c.rank(), reverse=True)
    return out


# --- Descarga ----------------------------------------------------------------

def download(url: str, dest: Path, profile, template: str = "flat", *,
             progress: Callable[[dict[str, Any]], None] | None = None,
             **job: Any) -> None:
    """Descarga desde YouTube con el motor yt-dlp y su postprocesado completo.

    Import diferido de `Downloader` a propósito: evita el ciclo de importación
    con `downloader.py`, que a su vez toma de aquí la política de YouTube.
    """
    from hifihub.engine.downloader import Downloader

    job.setdefault("player_clients", DEFAULT_PLAYER_CLIENTS)
    try:
        Downloader(progress=progress).download_audio(url, dest, profile, template, **job)
    except Exception as e:  # noqa: BLE001
        from hifihub.engine.downloader import humanize_error

        raise SourceError(humanize_error(str(e))) from e
