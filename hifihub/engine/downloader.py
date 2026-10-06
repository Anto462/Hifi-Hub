"""
Motor de descarga
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp

from hifihub import paths, profiles, templates
from hifihub.profiles import AudioProfile

ProgressCallback = Callable[[dict[str, Any]], None]

# Nombres de perfil.
AUDIO_PROFILES = tuple(profiles.PRESETS)


@dataclass(frozen=True)
class PlannedItem:
    """Una entrada por dry-run: qué se descargaría y dónde."""
    title: str
    dest_path: str
    duration: float | None = None


from hifihub.engine.sources.youtube import (  # noqa: E402
    DEFAULT_PLAYER_CLIENTS,
    JS_RUNTIMES,
    set_player_clients as _set_player_clients,
)

from contextlib import contextmanager  # noqa: E402


@contextmanager
def _null_cm():
    """Contextmanager que no aporta cookies."""
    yield None


def _base_opts(dest: Path, template: str) -> dict[str, Any]:
    return {
        "outtmpl": templates.build_outtmpl(dest, template),
        "ffmpeg_location": str(paths.tools_dir()),
        "windowsfilenames": True,
        "noplaylist": False,
        "js_runtimes": JS_RUNTIMES,
    }


def build_video_opts(dest: Path, template: str = "flat") -> dict[str, Any]:
    """Video HD (feature heredada de main.py)."""
    opts = _base_opts(dest, template)
    opts["format"] = "bestvideo[height>=720]+bestaudio/best"
    return opts


def build_audio_opts(
    dest: Path,
    profile: str | AudioProfile = "purista",
    template: str = "flat",
    use_archive: bool = False,
    sections: list[str] | str | None = None,
    exact_cuts: bool = False,
    split_chapters: bool = False,
    sponsorblock: bool | list[str] = False,
    cookies_browser: str = "",
    cookies_file: str = "",
    player_clients: str = DEFAULT_PLAYER_CLIENTS,
    pacing: float = 0.0,
) -> dict[str, Any]:
    """Opciones de yt-dlp para descargar audio según perfil y plantilla."""
    from hifihub.engine import sections as sections_mod
    from hifihub.engine import sponsorblock as sb_mod

    prof = profile if isinstance(profile, AudioProfile) else profiles.get_profile(profile)

    opts = _base_opts(dest, template)
    opts["format"] = "bestaudio/best"

    pps: list[dict[str, Any]] = []
    if sponsorblock:
        cats = sponsorblock if isinstance(sponsorblock, (list, tuple)) else sb_mod.DEFAULT_CATEGORIES
        pps += sb_mod.build_postprocessors(cats)
    pps += profiles.build_postprocessors(prof)
    if split_chapters:
        pps.append(sections_mod.chapter_postprocessor())
        opts["outtmpl"] = {
            "default": opts["outtmpl"],
            "chapter": os.path.join(str(dest), sections_mod.CHAPTER_OUTTMPL),
        }
    opts["postprocessors"] = pps

    if sections:
        sections_mod.apply_sections(opts, sections_mod.parse_ranges(sections), exact=exact_cuts)

    if use_archive:
        # Registro anti-duplicados de yt-dlp: los IDs ya descargados se omiten
        paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        opts["download_archive"] = str(paths.DOWNLOAD_ARCHIVE)

    if cookies_file:
        opts["cookiefile"] = cookies_file
        _set_player_clients(opts, player_clients)
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
        _set_player_clients(opts, player_clients)

    if pacing and pacing > 0:
        # Pausa aleatoria antes de cada descarga: intenta simular un humano y reducir el bloqueo por bot en álbumes/lotes.
        opts["sleep_interval"] = float(pacing)
        opts["max_sleep_interval"] = float(pacing) * 1.6

    extra_args = profiles.ffmpeg_postprocessor_args(prof)
    if extra_args:
        opts["postprocessor_args"] = {"extractaudio": extra_args}
    return opts


class Downloader:
    """Punto de entrada del núcleo para descargas individuales."""

    def __init__(self, progress: ProgressCallback | None = None) -> None:
        self._progress = progress

    def _run(self, url: str, opts: dict[str, Any]) -> None:
        if self._progress is not None:
            opts = {**opts, "progress_hooks": [self._progress]}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    def download_audio(
        self,
        url: str,
        dest: Path,
        profile: str | AudioProfile = "purista",
        template: str = "flat",
        enrich: bool = False,
        acoustid_key: str = "",
        analyze_bpm_key: bool = False,
        replaygain: bool = False,
        use_archive: bool = False,
        sections: list[str] | str | None = None,
        exact_cuts: bool = False,
        split_chapters: bool = False,
        sponsorblock: bool | list[str] = False,
        cookies_browser: str = "",
        cookies_file: str = "",
        player_clients: str = DEFAULT_PLAYER_CLIENTS,
        pacing: float = 0.0,
    ) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        from hifihub.engine import session as session_mod

        def _build(with_auth: bool) -> dict[str, Any]:
            opts = build_audio_opts(
                dest, profile, template, use_archive=use_archive,
                sections=sections, exact_cuts=exact_cuts,
                split_chapters=split_chapters, sponsorblock=sponsorblock,
                cookies_browser=cookies_browser if with_auth else "",
                cookies_file=cookies_file if with_auth else "",
                player_clients=player_clients, pacing=pacing,
            )
            if self._progress is not None:
                opts["progress_hooks"] = [self._progress]
            return opts

        def _run(with_auth: bool) -> dict[str, Any]:
            opts = _build(with_auth)
            # with_auth; se descifra a un temporal únicamente durante la descarga.
            cm = session_mod.session_cookiefile() if with_auth else _null_cm()
            with cm as sess:
                if with_auth and sess:
                    session_mod.apply_session_to_opts(opts, sess)
                    _set_player_clients(opts, player_clients)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=True)

        # ¿Hay autenticación disponible?
        has_auth = bool(cookies_browser or cookies_file) or session_mod.status().get("connected", False)
        try:
            info = _run(with_auth=has_auth)
        except yt_dlp.utils.DownloadError:
            if not has_auth:
                raise
            info = _run(with_auth=False)

        if enrich and info:
            self._enrich_all(info, acoustid_key, analyze_bpm_key)

        if replaygain and info:
            self._apply_replaygain(info)

    def _apply_replaygain(self, info: dict[str, Any]) -> None:
        from hifihub.audio.loudness import LoudnessError, scan_files

        files = [p for p in _final_paths(info) if Path(p).exists()]
        if not files:
            return
        try:
            scan_files(files, album=len(files) > 1)
        except LoudnessError:
            pass  # el volumen uniforme es un extra

    def _enrich_all(self, info: dict[str, Any], acoustid_key: str, analyze_bpm_key: bool = False) -> None:
        from hifihub.metadata.enrich import enrich_file

        entries = info.get("entries")
        items = list(entries) if entries is not None else [info]
        for entry in items:
            if not entry:
                continue
            filepath = _final_path(entry)
            if not filepath or not Path(filepath).exists():
                continue
            try:
                enrich_file(
                    filepath,
                    raw_title=entry.get("title", ""),
                    uploader=entry.get("uploader"),
                    artist=entry.get("artist"),
                    album=entry.get("album"),
                    date=_entry_year(entry),
                    track_number=entry.get("track_number"),
                    duration=entry.get("duration"),
                    thumbnail_url=entry.get("thumbnail"),
                    acoustid_key=acoustid_key,
                    analyze_bpm_key=analyze_bpm_key,
                )
            except Exception:  # enriquecer nunca debe tumbar la descarga
                pass

    def download_video(self, url: str, dest: Path, template: str = "flat") -> None:
        dest.mkdir(parents=True, exist_ok=True)
        self._run(url, build_video_opts(dest, template))

    def plan_audio(
        self,
        url: str,
        dest: Path,
        template: str = "flat",
    ) -> list[PlannedItem]:
        """
        Usa extract_info(download=False) + prepare_filename para que las rutas
        mostradas sean exactamente las de yt-dlp.
        """
        opts = build_audio_opts(dest, "purista", template)
        opts["quiet"] = True
        opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            entries = info.get("entries") if info else None
            items = list(entries) if entries is not None else [info]

            planned: list[PlannedItem] = []
            for entry in items:
                if not entry:
                    continue
                filename = ydl.prepare_filename(entry)
                planned.append(PlannedItem(
                    title=entry.get("title", "?"),
                    dest_path=os.path.normpath(filename),
                    duration=entry.get("duration"),
                ))
            return planned


def humanize_error(message: str) -> str:
    """Traduce los errores crípticos de yt-dlp a algo accionable para el usuario."""
    m = message.lower()
    if "not a bot" in m or "sign in to confirm" in m:
        return ("YouTube pide verificar que no eres un bot. Configura cookies en "
                "Ajustes: inicia sesión en YouTube en tu navegador, ciérralo por "
                "completo y elige ese navegador (Firefox es el más fiable). Si usas "
                "Chrome/Brave/Edge y sigue fallando, exporta un cookies.txt e indícalo.")
    if "could not copy" in m and "cookie" in m:
        return ("No se pudieron leer las cookies del navegador porque está abierto. "
                "Ciérralo por completo (Chrome/Brave/Edge bloquean su base de datos "
                "mientras están en ejecución) y reintenta, o usa Firefox / cookies.txt.")
    if "cookies" in m and ("could not find" in m or "not found" in m or "unable" in m):
        return ("No se encontraron cookies válidas en el navegador elegido. Asegúrate "
                "de haber iniciado sesión en YouTube en él, o usa un cookies.txt exportado.")
    if "drm protected" in m:
        return "El contenido está protegido con DRM y no se puede descargar."
    if "video unavailable" in m:
        return "El vídeo no está disponible (eliminado, privado o restringido por región)."
    if "private video" in m:
        return "Es un vídeo privado; necesitas cookies de una cuenta con acceso."
    if "requested format is not available" in m:
        return ("YouTube no ofrece un formato de audio descargable para este enlace. "
                "Si tienes sesión/cookies conectadas, tu cuenta autenticada exige un "
                "'PO Token' (restricción reciente de YouTube): prueba a cambiar los "
                "'Clientes de YouTube (avanzado)' en Ajustes, o desconecta la sesión "
                "para descargar sin autenticar. Si NO usas cookies, configúralas en "
                "Ajustes para acceder a los flujos de audio.")
    # Limpiar el prefijo 'ERROR:' repetido de yt-dlp para el resto.
    return message.replace("ERROR: ", "").strip()


def _final_path(entry: dict[str, Any]) -> str | None:
    """Ruta final del archivo tras el postprocesado."""
    reqs = entry.get("requested_downloads")
    if reqs:
        return reqs[-1].get("filepath") or reqs[-1].get("_filename")
    return entry.get("filepath")


def _final_paths(info: dict[str, Any]) -> list[str]:
    """Rutas finales de todas las entradas."""
    entries = info.get("entries")
    items = list(entries) if entries is not None else [info]
    out: list[str] = []
    for entry in items:
        if not entry:
            continue
        p = _final_path(entry)
        if p:
            out.append(p)
    return out


def _entry_year(entry: dict[str, Any]) -> str | None:
    year = entry.get("release_year")
    if year:
        return str(year)
    upload = entry.get("upload_date")  # YYYYMMDD
    return upload[:4] if upload and len(upload) >= 4 else None
