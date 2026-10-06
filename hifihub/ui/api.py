"""Puente js_api, El frontend los invoca como window.pywebview.api.*.

- Los métodos son finos: validan, delegan en el núcleo (hifihub.*) y devuelven
  datos JSON-serializables.
- Hilo del WebView: las descargas (yt-dlp es bloqueante) corren en un hilo aparte y reportan avance por el EventBus.
- La UI nunca importa lógica.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from hifihub import profiles, templates
from hifihub.audio import inspect as audio_inspect
from hifihub.config import Config
from hifihub.engine.downloader import Downloader
from hifihub.ui.events import EventBus


class Api:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._window: Any = None
        self._jobs: dict[str, threading.Thread] = {}
        self._job_counter = 0
        self._lock = threading.Lock()
        self._lib: Any = None
        self._controller: Any = None
        self._pl: Any = None

    def bind_window(self, window: Any) -> None:
        self._window = window

    # --- Biblioteca y reproductor--------------

    def _library(self):
        if self._lib is None:
            from hifihub.library.db import Library
            self._lib = Library()
        return self._lib

    def _player(self):
        # El controlador abre libmpv y el dispositivo de audio: solo al primer uso.
        if self._controller is None:
            from hifihub.player.controller import PlaybackController
            cfg = Config.load()
            scrobbler = None
            if cfg.lastfm_session_key:
                from hifihub.scrobble.lastfm import LastFmScrobbler
                scrobbler = LastFmScrobbler(
                    cfg.lastfm_api_key, cfg.lastfm_api_secret, cfg.lastfm_session_key
                )
            self._controller = PlaybackController(
                self._library(),
                on_event=lambda ch, payload: self._bus.emit(ch, payload),
                enable_smtc=True,
                scrobbler=scrobbler,
            )
            self._controller.set_replaygain(cfg.replaygain_mode)
            self._controller.set_replaygain_preamp(cfg.replaygain_preamp)
            # Ajustes del dispositivo antes del exclusivo: set_exclusive los aplica.
            self._controller.set_device_options(cfg.device_keepalive, cfg.device_wait_open)
            if cfg.limiter_enabled:
                self._controller.set_limiter(True)
            if cfg.listening_mode:
                self._controller.set_listening_mode(True)
            if cfg.wasapi_exclusive:
                self._controller.set_exclusive(True)
            if cfg.audio_device and cfg.audio_device != "auto":
                self._controller.set_audio_device(cfg.audio_device)
            if cfg.headphone_correction:
                self._controller.set_headphone(cfg.headphone_correction)
            if cfg.crossfeed:
                self._controller.set_crossfeed(cfg.crossfeed)
            if cfg.convolution:
                self._controller.set_convolution(cfg.convolution)
        return self._controller

    # --- Metadatos para poblar la UI -------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        return [
            {"name": p.name, "recodes": p.recodes,
             "lossless": p.lossless, "description": p.description}
            for p in profiles.PRESETS.values()
        ]

    def list_templates(self) -> list[str]:
        return list(templates.TEMPLATES)

    def get_config(self) -> dict[str, Any]:
        cfg = Config.load()
        return {
            "download_dir": cfg.download_dir,
            "default_profile": cfg.default_profile,
            "cookies_browser": cfg.cookies_browser,
            "batch_concurrency": cfg.batch_concurrency,
            "enrich_library": cfg.enrich_library,
            "acoustid_api_key": cfg.acoustid_api_key,
            "wasapi_exclusive": cfg.wasapi_exclusive,
            "apply_replaygain": cfg.apply_replaygain,
            "sponsorblock": cfg.sponsorblock,
            "cookies_file": cfg.cookies_file,
            "youtube_player_clients": cfg.youtube_player_clients,
            "download_pacing": cfg.download_pacing,
            "audio_device": cfg.audio_device,
            "analyze_bpm_key": cfg.analyze_bpm_key,
            "lastfm_session_key": cfg.lastfm_session_key,
            "replaygain_mode": cfg.replaygain_mode,
            "replaygain_preamp": cfg.replaygain_preamp,
            "multi_source": cfg.multi_source,
            "download_sources": cfg.download_sources,
            "spotify_client_id": cfg.spotify_client_id,
            "spotify_client_secret": cfg.spotify_client_secret,
            "headphone_correction": cfg.headphone_correction,
            "crossfeed": cfg.crossfeed,
            "convolution": cfg.convolution,
            "device_keepalive": cfg.device_keepalive,
            "device_wait_open": cfg.device_wait_open,
            "limiter_enabled": cfg.limiter_enabled,
            "listening_mode": cfg.listening_mode,
            "songs_per_page": cfg.songs_per_page,
            "grid_per_page": cfg.grid_per_page,
            "auto_check_updates": cfg.auto_check_updates,
            "ytdlp_channel": cfg.ytdlp_channel,
        }

    def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
        cfg = Config.load()
        if "download_dir" in data:
            cfg.download_dir = str(data["download_dir"])
        if "default_profile" in data and data["default_profile"] in profiles.PRESETS:
            cfg.default_profile = data["default_profile"]
        if "cookies_browser" in data:
            cfg.cookies_browser = str(data["cookies_browser"] or "")
        if "batch_concurrency" in data:
            try:
                cfg.batch_concurrency = max(1, int(data["batch_concurrency"]))
            except (TypeError, ValueError):
                pass
        if "download_pacing" in data:
            try:
                cfg.download_pacing = max(0.0, min(60.0, float(data["download_pacing"])))
            except (TypeError, ValueError):
                pass
        if "enrich_library" in data:
            cfg.enrich_library = bool(data["enrich_library"])
        if "acoustid_api_key" in data:
            cfg.acoustid_api_key = str(data["acoustid_api_key"] or "")
        if "wasapi_exclusive" in data:
            cfg.wasapi_exclusive = bool(data["wasapi_exclusive"])
            if self._controller is not None:
                self._controller.set_exclusive(cfg.wasapi_exclusive)
        if "apply_replaygain" in data:
            cfg.apply_replaygain = bool(data["apply_replaygain"])
        if "sponsorblock" in data:
            cfg.sponsorblock = bool(data["sponsorblock"])
        if "cookies_file" in data:
            cfg.cookies_file = str(data["cookies_file"] or "")
        if "youtube_player_clients" in data:
            cfg.youtube_player_clients = str(data["youtube_player_clients"] or "")
        if "analyze_bpm_key" in data:
            cfg.analyze_bpm_key = bool(data["analyze_bpm_key"])
        if "multi_source" in data:
            cfg.multi_source = bool(data["multi_source"])
        if "download_sources" in data:
            from hifihub.engine.sources import parse_selection
            cfg.download_sources = ",".join(parse_selection(data["download_sources"]))
        if "spotify_client_id" in data:
            cfg.spotify_client_id = str(data["spotify_client_id"] or "").strip()
        if "spotify_client_secret" in data:
            cfg.spotify_client_secret = str(data["spotify_client_secret"] or "").strip()
        if "auto_check_updates" in data:
            cfg.auto_check_updates = bool(data["auto_check_updates"])
        if "songs_per_page" in data:
            try:
                n = int(data["songs_per_page"])
                cfg.songs_per_page = n if n in (15, 25, 50) else 15
            except (TypeError, ValueError):
                pass
        if "grid_per_page" in data:
            try:
                n = int(data["grid_per_page"])
                cfg.grid_per_page = n if n in (15, 25, 50) else 25
            except (TypeError, ValueError):
                pass
        if "ytdlp_channel" in data:
            ch = str(data["ytdlp_channel"] or "stable")
            cfg.ytdlp_channel = ch if ch in ("stable", "nightly") else "stable"
        cfg.save()
        return self.get_config()

    # --- Diálogos nativos -------------------------------------------------

    def choose_folder(self) -> str | None:
        if self._window is None:
            return None
        import webview

        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else str(result)

    def choose_txt(self) -> str | None:
        if self._window is None:
            return None
        import webview

        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("Listas de enlaces (*.txt)", "Todos (*.*)")
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else str(result)

    # --- Dry-run ----------------------------------------------------------

    def plan_audio(self, url: str, dest: str, template: str = "flat") -> dict[str, Any]:
        try:
            plan = Downloader().plan_audio(url, Path(dest), template)
        except Exception as e:  # noqa: BLE001 — la UI muestra el mensaje
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "items": [
                {"title": it.title, "path": it.dest_path, "duration": it.duration}
                for it in plan
            ],
        }

    def inspect_file(self, path: str) -> dict[str, Any]:
        try:
            info = audio_inspect.probe(path)
        except audio_inspect.ProbeError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "label": info.quality_label(),
            "codec": info.codec,
            "sample_rate": info.sample_rate,
            "bit_depth": info.bit_depth,
            "channels": info.channels,
            "bit_rate_kbps": info.bit_rate_kbps,
            "duration": info.duration,
            "lossless": info.lossless,
        }

    # --- Actualizaciones (B1) ----------------------------------------------

    def check_updates(self, channel: str | None = None) -> dict[str, Any]:
        """Estado de actualización del motor de descargas. Tolerante a red caída."""
        from hifihub.engine.updater import check

        return check(channel or Config.load().ytdlp_channel)

    def apply_update(self, channel: str | None = None) -> dict[str, Any]:
        """Instala la última versión de yt-dlp del canal (overrides en exe, pip en venv).

        pywebview ejecuta cada llamada js_api en su propio hilo: no bloquea la UI."""
        from hifihub.engine.updater import UpdateError, apply_update

        try:
            return apply_update(channel or Config.load().ytdlp_channel)
        except UpdateError as e:
            return {"ok": False, "error": str(e)}

    # --- Sesión de YouTube (B2) --------------------------------------------

    def session_status(self) -> dict[str, Any]:
        from hifihub.engine import session

        return session.status()

    def session_connect(self, browser: str) -> dict[str, Any]:
        from hifihub.engine.session import SessionError, connect_from_browser

        try:
            meta = connect_from_browser(browser)
        except SessionError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **meta}

    def session_connect_file(self) -> dict[str, Any]:
        """Importa un cookies.txt vía diálogo nativo, lo filtra y lo cifra."""
        if self._window is None:
            return {"ok": False, "error": "Sin ventana."}
        import webview

        from hifihub.engine.session import SessionError, connect_from_file

        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("cookies.txt (*.txt)", "Todos (*.*)")
        )
        if not result:
            return {"ok": False, "error": "Cancelado."}
        path = result[0] if isinstance(result, (list, tuple)) else str(result)
        try:
            meta = connect_from_file(path)
        except SessionError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **meta}

    def session_disconnect(self) -> dict[str, Any]:
        from hifihub.engine import session

        session.disconnect()
        return {"ok": True}

    # --- Biblioteca -------------------------------------------------------

    def scan_library(self) -> dict[str, Any]:
        from hifihub.library import scanner

        cfg = Config.load()
        lib = self._library()
        result = scanner.scan([Path(cfg.download_dir)], lib)
        self._bus.emit("library:scanned", {
            "added": result.added, "updated": result.updated,
            "removed": result.removed, "errors": result.errors,
        })
        return {"ok": True, **lib.stats()}

    def list_albums(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._library().list_albums()]

    def album_tracks(self, album: str | None) -> list[dict[str, Any]]:
        return [dict(r) for r in self._library().album_tracks(album)]

    def search_library(self, query: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._library().search(query)]

    def list_artists(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._library().list_artists()]

    def artist_page(self, name: str | None) -> dict[str, Any]:
        """Todo lo necesario para la pantalla de un artista en una llamada."""
        lib = self._library()
        # Si se llega desde una colaboración plegada, mostrar el artista principal.
        name = lib.resolve_artist(name)
        albums = [dict(r) for r in lib.list_albums(artist=name)]
        tracks = [dict(r) for r in lib.tracks_by_artist(name)]
        return {
            "name": name,
            "albums": albums,
            "tracks": tracks,
            "track_count": len(tracks),
            "total_duration": sum(t.get("duration") or 0 for t in tracks),
        }

    def all_tracks(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._library().list_all_tracks()]

    def get_track(self, track_id: int) -> dict[str, Any] | None:
        row = self._library().get_track(int(track_id))
        return dict(row) if row else None

    # --- Importar música propia (E2) ---------------------------------------

    def choose_audio_files(self) -> list[str]:
        """Diálogo nativo multi-selección de archivos de audio."""
        if self._window is None:
            return []
        import webview

        exts = "*.flac;*.wav;*.aiff;*.alac;*.m4a;*.mp3;*.opus;*.ogg;*.aac"
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=(f"Audio ({exts})", "Todos (*.*)"),
        )
        if not result:
            return []
        return list(result) if isinstance(result, (list, tuple)) else [str(result)]

    def start_import(self, opts: dict[str, Any]) -> dict[str, Any]:
        """Importa archivos/carpetas a la biblioteca en un hilo. Eventos import:*."""
        from hifihub.engine.importer import collect_audio

        inputs = opts.get("inputs") or []
        if not inputs:
            return {"ok": False, "error": "No se eligieron archivos ni carpetas."}
        files = collect_audio(inputs)
        if not files:
            return {"ok": False, "error": "No se encontraron archivos de audio."}

        cfg = Config.load()
        dest = Path(opts.get("dest") or cfg.download_dir)
        job = dict(
            move=bool(opts.get("move")),
            enrich=bool(opts.get("enrich", cfg.enrich_library)),
            replaygain=bool(opts.get("replaygain", cfg.apply_replaygain)),
            convert_flac=bool(opts.get("convert_flac")),
            acoustid_key=cfg.acoustid_api_key,
            analyze_bpm_key=cfg.analyze_bpm_key,
        )

        def run() -> None:
            from hifihub.engine.importer import import_files
            import_files(inputs, dest, on_event=lambda ch, p: self._bus.emit(ch, p),
                         lib=self._library(), **job)

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "total": len(files)}

    # --- Edición y acciones por pista (C3) ---------------------------------

    def update_track(self, track_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """Edita los tags de una pista (archivo primero, luego BD)."""
        from hifihub.library.editing import EditError, apply_tag_edit

        lib = self._library()
        row = lib.get_track(int(track_id))
        # Si es la pista que suena, el archivo está bloqueado en Windows: parar.
        if row is not None and self._controller is not None:
            if self._controller.current_path() == row["path"]:
                self._controller.stop()
        try:
            updated = apply_tag_edit(lib, int(track_id), fields)
        except EditError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "track": updated}

    def play_track(self, track_id: int) -> dict[str, Any]:
        """Reproduce una sola pista ahora (reemplaza la cola)."""
        row = self._library().get_track(int(track_id))
        if row is None:
            return {"ok": False, "error": "Pista no encontrada."}
        self._player().play_tracks([row])
        return {"ok": True}

    def add_to_queue(self, track_ids: list[int]) -> dict[str, Any]:
        lib = self._library()
        rows = [lib.get_track(int(t)) for t in track_ids]
        rows = [r for r in rows if r]
        if not rows:
            return {"ok": False, "error": "Sin pistas."}
        n = self._player().add_to_queue(rows)
        return {"ok": True, "added": n}

    def reveal_in_folder(self, track_id: int) -> dict[str, Any]:
        import subprocess

        row = self._library().get_track(int(track_id))
        if row is None or not Path(row["path"]).exists():
            return {"ok": False, "error": "El archivo no existe."}
        # /select, resalta el archivo en el Explorador de Windows.
        subprocess.Popen(["explorer", "/select,", str(Path(row["path"]))])
        return {"ok": True}

    def remove_from_library(self, track_id: int) -> dict[str, Any]:
        """Quita la pista de la biblioteca (la fila). NO borra el archivo."""
        self._library().delete_track(int(track_id))
        return {"ok": True}

    @staticmethod
    def _recycle(path: str) -> bool:
        """Envía un archivo a la Papelera de reciclaje de Windows (reversible).

        Usa SHFileOperationW con FOF_ALLOWUNDO en vez de un borrado permanente,
        para que el usuario pueda restaurar. Devuelve True si se envió a la Papelera.
        """
        import ctypes
        from ctypes import wintypes

        p = Path(path)
        if not p.exists():
            return False

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE = 3
        FOF_SILENT = 0x0004
        FOF_NOCONFIRMATION = 0x0010
        FOF_ALLOWUNDO = 0x0040
        FOF_NOERRORUI = 0x0400

        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        # pFrom debe terminar en doble NUL (lista de rutas). LPCWSTR añade uno.
        op.pFrom = str(p) + "\x00"
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return res == 0 and not op.fAnyOperationsAborted

    def delete_track(self, track_id: int) -> dict[str, Any]:
        """Elimina una pista: archivo a la Papelera + fila de la biblioteca."""
        lib = self._library()
        row = lib.get_track(int(track_id))
        if row is None:
            return {"ok": False, "error": "Pista no encontrada."}
        recycled = self._recycle(row["path"])
        lib.delete_track(int(track_id))
        return {"ok": True, "recycled": recycled}

    def delete_album(self, album: str | None) -> dict[str, Any]:
        """Elimina un álbum completo: cada archivo a la Papelera + filas."""
        lib = self._library()
        rows = lib.album_tracks(album)
        recycled = 0
        for r in rows:
            if self._recycle(r["path"]):
                recycled += 1
            lib.delete_track(r["id"])
        return {"ok": True, "count": len(rows), "recycled": recycled}

    # --- Playlists (C2) ----------------------------------------------------

    def _playlists(self):
        if self._pl is None:
            from hifihub.library.playlists import Playlists
            self._pl = Playlists(self._library())
        return self._pl

    def playlists_list(self) -> list[dict[str, Any]]:
        return self._playlists().list_all()

    def playlist_create(self, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "Ponle un nombre a la playlist."}
        pid = self._playlists().create(name)
        return {"ok": True, "id": pid, "name": name}

    def playlist_rename(self, playlist_id: int, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "El nombre no puede estar vacío."}
        self._playlists().rename(int(playlist_id), name)
        return {"ok": True}

    def playlist_delete(self, playlist_id: int) -> dict[str, Any]:
        self._playlists().delete(int(playlist_id))
        return {"ok": True}

    def playlist_tracks(self, playlist_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self._playlists().tracks(int(playlist_id))]

    def playlist_add(self, playlist_id: int, track_id: int) -> dict[str, Any]:
        self._playlists().add_track(int(playlist_id), int(track_id))
        return {"ok": True}

    def playlist_add_many(self, playlist_id: int, track_ids: list[int]) -> dict[str, Any]:
        """Añade una colección entera (álbum, artista, mezcla) a una playlist."""
        res = self._playlists().add_tracks(int(playlist_id), track_ids or [])
        return {"ok": True, **res}

    def playlist_create_with(self, name: str, track_ids: list[int]) -> dict[str, Any]:
        """Crea una playlist nueva ya rellena (lo usan las mezclas de «Para ti»)."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "Ponle un nombre a la playlist."}
        pl = self._playlists()
        pid = pl.create(name)
        res = pl.add_tracks(pid, track_ids or [])
        return {"ok": True, "id": pid, "name": name, **res}

    def playlist_remove(self, playlist_id: int, track_id: int) -> dict[str, Any]:
        self._playlists().remove_track(int(playlist_id), int(track_id))
        return {"ok": True}

    def playlist_move(self, playlist_id: int, position: int, delta: int) -> dict[str, Any]:
        moved = self._playlists().move(int(playlist_id), int(position), int(delta))
        return {"ok": moved}

    def play_playlist(self, playlist_id: int, start: int = 0) -> dict[str, Any]:
        rows = self._playlists().tracks(int(playlist_id))
        if not rows:
            return {"ok": False, "error": "La playlist está vacía."}
        self._player().play_tracks(rows, start=int(start))
        return {"ok": True}

    def playlist_export(self, playlist_id: int) -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "error": "Sin ventana."}
        import webview

        pl = next((p for p in self._playlists().list_all() if p["id"] == int(playlist_id)), None)
        suggested = (pl["name"] if pl else "playlist") + ".m3u8"
        result = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename=suggested)
        if not result:
            return {"ok": False, "error": "Cancelado."}
        dest = result[0] if isinstance(result, (list, tuple)) else str(result)
        try:
            out = self._playlists().export_m3u8(int(playlist_id), dest)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": str(out)}

    def playlist_import(self) -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "error": "Sin ventana."}
        import webview

        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("Playlists (*.m3u8;*.m3u)", "Todos (*.*)")
        )
        if not result:
            return {"ok": False, "error": "Cancelado."}
        src = result[0] if isinstance(result, (list, tuple)) else str(result)
        try:
            pid = self._playlists().import_m3u8(src)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        added = len(self._playlists().tracks(pid))
        return {"ok": True, "id": pid, "added": added,
                "note": "Solo se añaden pistas que ya están en la biblioteca."}

    def library_stats(self) -> dict[str, Any]:
        return self._library().stats()

    def cover_uri(self, cover_path: str | None) -> str | None:
        """Portada como data URI (evita problemas de origen file:// en el WebView)."""
        if not cover_path or not Path(cover_path).exists():
            return None
        import base64

        data = Path(cover_path).read_bytes()
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")

    # --- Reproductor ------------------------------------------------------

    def play_album(self, album: str | None) -> dict[str, Any]:
        self._player().play_album(album)
        return {"ok": True}

    def play_album_from(self, album: str | None, index: int) -> dict[str, Any]:
        rows = self._library().album_tracks(album)
        self._player().play_tracks(rows, start=index)
        return {"ok": True}

    def play_artist(self, name: str | None, shuffle: bool = False) -> dict[str, Any]:
        rows = self._library().tracks_by_artist(name)
        if not rows:
            return {"ok": False, "error": "Sin pistas de este artista."}
        ctrl = self._player()
        ctrl.set_shuffle(bool(shuffle))
        ctrl.play_tracks(rows)
        return {"ok": True, "total": len(rows)}

    def play_artist_from(self, name: str | None, index: int) -> dict[str, Any]:
        rows = self._library().tracks_by_artist(name)
        self._player().play_tracks(rows, start=int(index))
        return {"ok": True}

    def play_all_from(self, index: int) -> dict[str, Any]:
        """Reproduce toda la biblioteca desde la fila `index` de la vista
        Canciones (mismo orden que all_tracks)."""
        rows = self._library().list_all_tracks()
        self._player().play_tracks(rows, start=int(index))
        return {"ok": True}

    def play_ids(self, track_ids: list[int], start: int = 0) -> dict[str, Any]:
        """Reproduce EXACTAMENTE esta lista de pistas, en este orden.

        Preferible a `play_all_from` cuando la vista puede reordenar o filtrar: el
        orden viaja explícito en vez de depender de que la UI y la consulta del
        backend coincidan índice a índice.
        """
        lib = self._library()
        rows = []
        for tid in track_ids or []:
            try:
                row = lib.get_track(int(tid))
            except (TypeError, ValueError):
                continue
            if row is not None:
                rows.append(row)
        if not rows:
            return {"ok": False, "error": "Nada que reproducir."}
        start = max(0, min(int(start or 0), len(rows) - 1))
        self._player().play_tracks(rows, start=start)
        # Se devuelve `start` ya acotado: es la única forma determinista de
        # comprobarlo, porque `_index` lo sobrescribe después el observador de
        # playlist-pos de mpv mientras reconstruye la lista.
        return {"ok": True, "count": len(rows), "start": start}

    def player_play_index(self, index: int) -> dict[str, Any]:
        """Salta a una posición de la cola (desde el panel de Cola)."""
        self._player().play_index(int(index))
        return {"ok": True}

    def player_toggle(self) -> dict[str, Any]:
        return self._player().toggle_pause()

    def player_next(self) -> dict[str, Any]:
        self._player().next()
        return {"ok": True}

    def player_prev(self) -> dict[str, Any]:
        self._player().prev()
        return {"ok": True}

    def player_seek(self, seconds: float) -> dict[str, Any]:
        self._player().seek(float(seconds))
        return {"ok": True}

    def player_volume(self, volume: float) -> dict[str, Any]:
        self._player().set_volume(float(volume))
        return {"ok": True}

    def player_shuffle(self, enabled: bool) -> dict[str, Any]:
        self._player().set_shuffle(bool(enabled))
        return {"ok": True}

    def player_repeat(self, mode: str) -> dict[str, Any]:
        self._player().set_repeat(str(mode))
        return {"ok": True}

    def player_exclusive(self, enabled: bool) -> dict[str, Any]:
        self._player().set_exclusive(bool(enabled))
        return {"ok": True}

    def player_devices(self) -> list[dict[str, Any]]:
        return self._player().audio_devices()

    def player_set_device(self, device: str) -> dict[str, Any]:
        cfg = Config.load()
        cfg.audio_device = device or "auto"
        cfg.save()
        self._player().set_audio_device(cfg.audio_device)
        return {"ok": True}

    def player_queue(self) -> dict[str, Any]:
        return self._player().get_queue()

    def player_play_file(self, path: str) -> dict[str, Any]:
        self._player().play_file(path)
        return {"ok": True}

    def set_replaygain(self, mode: str | None = None, preamp: float | None = None) -> dict[str, Any]:
        cfg = Config.load()
        ctrl = self._player()
        if mode is not None:
            cfg.replaygain_mode = mode if mode in ("off", "track", "album") else "track"
            ctrl.set_replaygain(cfg.replaygain_mode)
        if preamp is not None:
            try:
                cfg.replaygain_preamp = max(0.0, min(12.0, float(preamp)))
                ctrl.set_replaygain_preamp(cfg.replaygain_preamp)
            except (TypeError, ValueError):
                pass
        cfg.save()
        return {"mode": cfg.replaygain_mode, "preamp": cfg.replaygain_preamp}

    # --- Experiencia audiófila ---------------------------------------------

    def signal_path(self) -> dict[str, Any]:
        return self._player().signal_path()

    def get_eq(self) -> dict[str, Any]:
        return self._player().eq_state()

    def set_eq(self, gains: list[float] | None = None, enabled: bool | None = None,
               freqs: list[float] | None = None) -> dict[str, Any]:
        try:
            return self._player().set_eq(gains, enabled, freqs)
        except ValueError as e:
            return {"error": str(e)}

    def reset_eq_freqs(self) -> dict[str, Any]:
        """Devuelve las frecuencias centrales a las ISO por defecto."""
        return self._player().set_eq(freqs=[])      # lista vacía = restaurar ISO

    def set_eq_solo(self, band: int | None) -> dict[str, Any]:
        """Aísla una banda para afinarla de oído (None = quitar el aislamiento)."""
        return self._player().set_eq_solo(band)

    def list_eq_presets(self) -> list[dict[str, Any]]:
        """Presets de fábrica + los del usuario, marcados para poder agruparlos."""
        from hifihub.player.eq_presets import list_factory, list_presets

        user = [{**p, "factory": False} for p in list_presets()]
        return list_factory() + user

    def save_eq_preset(self, name: str) -> dict[str, Any]:
        from hifihub.player.eq_presets import PresetError, save_preset

        st = self._player().eq_state()
        try:
            return {"ok": True, "name": save_preset(name, st["gains"], st["freqs"])}
        except PresetError as e:
            return {"ok": False, "error": str(e)}

    def load_eq_preset(self, name: str, factory: bool = False) -> dict[str, Any]:
        from hifihub.player.eq_presets import PresetError, load_factory, load_preset

        try:
            data = load_factory(name) if factory else load_preset(name)
        except PresetError as e:
            return {"ok": False, "error": str(e)}
        state = self._player().set_eq(data["gains"], True, data.get("freqs"))
        return {**state, "why": data.get("why", "")}

    def delete_eq_preset(self, name: str) -> dict[str, Any]:
        from hifihub.player.eq_presets import delete_preset

        delete_preset(name)
        return {"ok": True}

    # --- Dispositivo y protección de picos------------------------

    def get_device_options(self) -> dict[str, Any]:
        cfg = Config.load()
        return {"keepalive": cfg.device_keepalive, "wait_open": cfg.device_wait_open,
                "limiter": cfg.limiter_enabled, "exclusive": cfg.wasapi_exclusive}

    def set_listening_mode(self, enabled: bool) -> dict[str, Any]:
        cfg = Config.load()
        cfg.listening_mode = bool(enabled)
        cfg.save()
        return self._player().set_listening_mode(cfg.listening_mode)

    def set_device_options(self, keepalive: bool | None = None,
                           wait_open: float | None = None,
                           limiter: bool | None = None) -> dict[str, Any]:
        cfg = Config.load()
        if keepalive is not None:
            cfg.device_keepalive = bool(keepalive)
        if wait_open is not None:
            try:
                cfg.device_wait_open = max(0.0, min(5.0, float(wait_open)))
            except (TypeError, ValueError):
                pass
        if limiter is not None:
            cfg.limiter_enabled = bool(limiter)
            self._player().set_limiter(cfg.limiter_enabled)
        cfg.save()
        self._player().set_device_options(cfg.device_keepalive, cfg.device_wait_open)
        return self.get_device_options()

    # --- Corrección de auriculares y crossfeed------------------------

    def list_headphones(self) -> list[dict[str, str]]:
        from hifihub.player.headphones import list_presets
        return list_presets()

    def set_headphone(self, name: str | None) -> dict[str, Any]:
        cfg = Config.load()
        cfg.headphone_correction = name or ""
        cfg.save()
        self._player().set_headphone(name or None)
        return {"headphone": cfg.headphone_correction}

    def import_headphone(self) -> dict[str, Any]:
        """Diálogo nativo para importar un ParametricEQ.txt (de autoeq.app)."""
        if self._window is None:
            return {"ok": False, "error": "Sin ventana."}
        import webview
        from hifihub.player.headphones import HeadphoneError, import_preset

        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("AutoEq ParametricEQ (*.txt)", "Todos (*.*)")
        )
        if not result:
            return {"ok": False, "error": "Cancelado."}
        path = result[0] if isinstance(result, (list, tuple)) else str(result)
        try:
            name = import_preset(path)
        except (HeadphoneError, OSError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "name": name}

    # --- «Para ti»: mezclas calculadas en local ---------------------------

    def for_you(self) -> dict[str, Any]:
        """Mezclas y cifras para la pantalla «Para ti». Todo se calcula aquí:
        no sale nada del equipo ni se consulta ningún servicio."""
        from hifihub.library.recommend import build_mixes, build_strips, headline_stats

        lib = self._library()
        return {"stats": headline_stats(lib), "mixes": build_mixes(lib),
                "strips": build_strips(lib)}

    # --- Convolución por impulso---------------------------------

    def list_impulses(self) -> list[dict[str, Any]]:
        from hifihub.player.convolution import list_impulses

        return list_impulses()

    def get_convolution(self) -> dict[str, Any]:
        return {"convolution": Config.load().convolution}

    def set_convolution(self, name: str | None) -> dict[str, Any]:
        cfg = Config.load()
        cfg.convolution = name or ""
        cfg.save()
        return self._player().set_convolution(cfg.convolution or None)

    def import_impulse(self) -> dict[str, Any]:
        """Diálogo nativo para importar un .wav de respuesta al impulso (REW…)."""
        if self._window is None:
            return {"ok": False, "error": "Sin ventana."}
        import webview

        files = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("Impulso (*.wav;*.flac;*.aiff)", "Todos (*.*)"),
        )
        if not files:
            return {"ok": False, "error": "Cancelado."}
        from hifihub.player.convolution import ConvolutionError, import_impulse

        try:
            return {"ok": True, **import_impulse(files[0])}
        except ConvolutionError as e:
            return {"ok": False, "error": str(e)}

    def delete_impulse(self, name: str) -> dict[str, Any]:
        from hifihub.player.convolution import delete_impulse

        cfg = Config.load()
        if cfg.convolution == name:      # si estaba activo, desactivarlo primero
            cfg.convolution = ""
            cfg.save()
            self._player().set_convolution(None)
        delete_impulse(name)
        return {"ok": True}

    def set_crossfeed(self, strength: float) -> dict[str, Any]:
        cfg = Config.load()
        try:
            cfg.crossfeed = max(0.0, min(1.0, float(strength)))
        except (TypeError, ValueError):
            cfg.crossfeed = 0.0
        cfg.save()
        self._player().set_crossfeed(cfg.crossfeed)
        return {"crossfeed": cfg.crossfeed}

    def get_lyrics(self, path: str) -> dict[str, Any]:
        from hifihub.metadata.lyrics import parse_lrc, read_lyrics

        try:
            text = read_lyrics(path)
        except Exception:  # noqa: BLE001
            text = None
        if not text:
            return {"found": False, "synced": False, "lines": []}
        return {"found": True, **parse_lrc(text)}

    def spectrogram(self, path: str) -> dict[str, Any]:
        from hifihub.audio.spectrum import SpectrumError, spectrogram_png

        import base64
        try:
            png = spectrogram_png(path)
        except SpectrumError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "uri": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}

    def spectral_check(self, path: str) -> dict[str, Any]:
        from hifihub.audio.spectrum import SpectrumError, analyze

        try:
            a = analyze(path)
        except SpectrumError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True, "cutoff_khz": a.cutoff_khz,
            "nyquist_khz": a.nyquist_hz / 1000, "ratio": a.ratio,
            "suspicious": a.suspicious, "verdict": a.verdict,
        }

    # --- Estudio-procesado offline-----------------------------------

    def studio_available(self) -> dict[str, Any]:
        from hifihub.studio import chain, mastering
        return {"pedalboard": chain.AVAILABLE, "matchering": mastering.AVAILABLE}

    def studio_catalog(self) -> dict[str, Any]:
        from hifihub.studio import presets
        from hifihub.studio.chain import CATALOG
        return {
            "effects": [{"type": e.type, "label": e.label, "params": e.params} for e in CATALOG],
            "presets": presets.list_presets(),
        }

    def studio_get_preset(self, name: str) -> list[dict[str, Any]]:
        from hifihub.studio import presets
        try:
            return presets.get_preset_chain(name)
        except KeyError:
            return []

    def studio_scan_vst3(self) -> list[dict[str, str]]:
        from hifihub.studio.plugins import scan_vst3
        return [{"name": p.name, "path": p.path} for p in scan_vst3()]

    def studio_process(self, opts: dict[str, Any]) -> dict[str, Any]:
        """Procesa una pista/álbum a copias en un hilo. Avance por eventos studio:*."""
        from hifihub.studio.chain import AVAILABLE
        if not AVAILABLE:
            return {"ok": False, "error": "Instala el extra 'studio' (pedalboard)."}

        track_ids = opts.get("track_ids") or []
        chain = opts.get("chain") or []
        if not track_ids or not chain:
            return {"ok": False, "error": "Faltan pistas o efectos."}

        lib = self._library()
        rows = [lib.get_track(int(t)) for t in track_ids]
        paths_ = [r["path"] for r in rows if r]
        out_dir = opts.get("out_dir") or None

        threading.Thread(
            target=self._run_studio, args=(paths_, chain, out_dir), daemon=True
        ).start()
        return {"ok": True, "total": len(paths_)}

    def _run_studio(self, paths_: list[str], chain: list, out_dir: str | None) -> None:
        from hifihub.studio.chain import StudioError, default_output_path, process_file

        done = 0
        added = 0
        for src in paths_:
            try:
                dst = default_output_path(src, out_dir)
                process_file(src, dst, chain)
                self._finalize_studio_output(src, dst)
                added += 1
                self._bus.emit("studio:item", {"src": src, "dst": str(dst), "ok": True,
                                               "done": done + 1, "total": len(paths_)})
            except (StudioError, Exception) as e:  # noqa: BLE001
                self._bus.emit("studio:item", {"src": src, "ok": False, "error": str(e),
                                               "done": done + 1, "total": len(paths_)})
            done += 1
        self._bus.emit("studio:complete", {"total": len(paths_), "added": added})

    def _finalize_studio_output(self, src: str, dst: Path) -> None:
        """Da a la copia procesada los metadatos del original + ReplayGain + alta
        en la biblioteca, para que aparezca junto a la original al instante."""
        from hifihub.studio.chain import copy_metadata

        copy_metadata(src, dst)   # tags + portada + letra (título con «(procesado)»)
        try:
            from hifihub.audio.loudness import LoudnessError, scan_files
            scan_files([dst])     # volumen uniforme como el resto de la biblioteca
        except (LoudnessError, Exception):  # extra, no debe abortar
            pass
        try:
            from hifihub.library import scanner
            self._library().upsert_track(scanner._build_track(dst, dst.stat().st_mtime))
        except Exception:  # noqa: BLE001
            pass

    def studio_preview(self, path: str, chain: list, seconds: float = 20.0) -> dict[str, Any]:
        """Genera un clip corto procesado (para A/B) y devuelve su ruta temporal."""
        from hifihub.studio.chain import AVAILABLE, StudioError, _mkstemp_closed, _run_ffmpeg, process_file
        if not AVAILABLE:
            return {"ok": False, "error": "pedalboard no instalado."}
        try:
            clip = _mkstemp_closed(".flac")
            _run_ffmpeg(["-t", str(seconds), "-i", path, "-c:a", "flac", str(clip)])
            out = _mkstemp_closed(".flac")
            process_file(clip, out, chain)
            clip.unlink(missing_ok=True)
            return {"ok": True, "original": path, "processed": str(out)}
        except (StudioError, OSError) as e:
            return {"ok": False, "error": str(e)}

    def studio_master(self, target: str, reference: str, out_dir: str | None = None) -> dict[str, Any]:
        from hifihub.studio.chain import default_output_path
        from hifihub.studio.mastering import AVAILABLE, StudioError, match_reference
        if not AVAILABLE:
            return {"ok": False, "error": "Instala el extra 'studio' (matchering)."}

        def run():
            try:
                dst = default_output_path(target, out_dir)
                match_reference(target, reference, dst)
                self._finalize_studio_output(target, dst)
                self._bus.emit("studio:complete", {"total": 1, "dst": str(dst), "added": 1})
            except (StudioError, Exception) as e:  # ex
                self._bus.emit("studio:item", {"src": target, "ok": False, "error": str(e)})

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    # --- Descargas --------------------------------------------------------

    def start_download(self, opts: dict[str, Any]) -> dict[str, Any]:
        """Lanza una descarga en segundo plano. Devuelve {job_id} de inmediato.

        opts: url, dir, profile, template, sample_rate?, bit_depth?
        El avance llega por eventos: download:progress / :complete / :error.
        """
        url = (opts.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "El enlace no puede estar vacío."}

        cfg = Config.load()
        dest = Path(opts.get("dir") or cfg.download_dir)
        template = opts.get("template") or "flat"

        try:
            prof = profiles.get_profile(opts.get("profile") or cfg.default_profile)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if prof.recodes and (opts.get("sample_rate") or opts.get("bit_depth")):
            prof = prof.with_resample(
                _int_or_none(opts.get("sample_rate")),
                _int_or_none(opts.get("bit_depth")),
            )

        sections = opts.get("sections") or None
        if sections:
            from hifihub.engine.sections import SectionError, parse_ranges
            try:
                parse_ranges(sections)  # validar antes de lanzar el hilo
            except SectionError as e:
                return {"ok": False, "error": str(e)}

        job = dict(
            enrich=bool(opts.get("enrich", cfg.enrich_library)),
            acoustid_key=cfg.acoustid_api_key,
            analyze_bpm_key=cfg.analyze_bpm_key,
            replaygain=cfg.apply_replaygain,
            sections=sections,
            exact_cuts=bool(opts.get("exact_cuts")),
            split_chapters=bool(opts.get("split_chapters")),
            sponsorblock=bool(opts.get("sponsorblock", cfg.sponsorblock)),
            cookies_browser=cfg.cookies_browser,
            cookies_file=cfg.cookies_file,
            player_clients=cfg.youtube_player_clients,
            pacing=cfg.download_pacing,
        )
        # los timestamps son del video original.
        job["multi_source"] = bool(opts.get("multi_source", cfg.multi_source)) and not sections
        # Fuentes habilitadas (youtube / archive). Una sola = se usa esa sí o sí;
        # las dos = gana la de mayor calidad real.
        job["sources"] = opts.get("sources") or cfg.download_sources
        job["spotify_client_id"] = cfg.spotify_client_id
        job["spotify_client_secret"] = cfg.spotify_client_secret

        with self._lock:
            self._job_counter += 1
            job_id = f"job{self._job_counter}"

        thread = threading.Thread(
            target=self._run_download,
            args=(job_id, url, dest, prof, template, job),
            daemon=True,
        )
        self._jobs[job_id] = thread
        thread.start()
        return {"ok": True, "job_id": job_id}

    def _run_download(self, job_id, url, dest, prof, template, job) -> None:
        def hook(d: dict[str, Any]) -> None:
            self._bus.emit("download:progress", _progress_payload(job_id, d))

        def stage(msg: str) -> None:
            self._bus.emit("download:progress", {
                "job_id": job_id, "status": "searching", "stage": msg,
            })

        self._bus.emit("download:progress", {
            "job_id": job_id, "status": "starting", "percent": 0.0,
        })
        multi = job.pop("multi_source", False)
        selection = job.pop("sources", "")
        sp_id = job.pop("spotify_client_id", "")
        sp_secret = job.pop("spotify_client_secret", "")
        try:
            from hifihub.engine import sources as src

            # El postprocesado es común a todas las fuentes; el resto del job son
            # opciones de yt-dlp que solo entiende la lane de YouTube.
            post = src.PostProcess(
                enrich=job.pop("enrich", True),
                acoustid_key=job.pop("acoustid_key", ""),
                analyze_bpm_key=job.pop("analyze_bpm_key", False),
                replaygain=job.pop("replaygain", True),
            )
            selected = src.parse_selection(selection)

            # "Buscar mejor origen" (multi_source) sigue siendo el comparador
            # dentro de yt-dlp (incluye SoundCloud) y solo aplica a YouTube.
            if multi and selected == [src.youtube.NAME]:
                url = self._resolve_source(job_id, url, job)

            res = src.resolve(
                url, selected=selected, spotify_client_id=sp_id,
                spotify_client_secret=sp_secret, progress=stage,
            )

            if res.kind == "tracks":
                self._download_wanted(job_id, res, dest, prof, template, job,
                                      post, selected, hook, stage)
            else:
                self._emit_sources(job_id, res)
                src.download_candidate(
                    res.chosen, dest, profile=prof, template=template,
                    post=post, yt_job=job, progress=hook, stage=stage,
                )
            self._bus.emit("download:complete", {"job_id": job_id})
        except Exception as e:  # noqa: BLE001
            from hifihub.engine.downloader import humanize_error
            self._bus.emit("download:error", {"job_id": job_id, "error": humanize_error(str(e))})
        finally:
            self._jobs.pop(job_id, None)

    def _emit_sources(self, job_id: str, res) -> None:
        """Manda a la UI la comparación de fuentes y cuál se eligió."""
        if not res.candidates:
            return
        from hifihub.engine import sources as src

        self._bus.emit("download:sources", {
            "job_id": job_id,
            "sources": [
                {"source": src.source_label(c.source), "quality": c.quality.label(),
                 "match": c.match_score, "original": c.is_original_input,
                 "chosen": c is res.chosen, "title": c.title,
                 "tracks": c.track_count, "notes": c.notes}
                for c in res.candidates
            ],
        })

    def _download_wanted(self, job_id, res, dest, prof, template, job, post,
                         selected, hook, stage) -> None:
        """Catálogo (playlist/álbum de Spotify): resuelve y baja canción a canción."""
        from hifihub.engine import sources as src

        total = len(res.wanted)
        ok = 0
        for i, wanted in enumerate(res.wanted, start=1):
            stage(f"[{i}/{total}] {wanted.query or wanted.title}")
            try:
                one = src.resolve_track(wanted, selected=selected, progress=stage)
                self._emit_sources(job_id, one)
                src.download_candidate(
                    one.chosen, dest, profile=prof, template=template,
                    post=post, yt_job=job, progress=hook, stage=stage,
                )
                ok += 1
            except Exception as e:  # noqa: BLE001 — una canción no aborta la lista
                self._bus.emit("download:progress", {
                    "job_id": job_id, "status": "searching",
                    "stage": f"[{i}/{total}] sin resultado: {e}",
                })
        stage(f"Catálogo terminado: {ok}/{total} descargadas.")

    def _resolve_source(self, job_id: str, url: str, job: dict[str, Any]) -> str:
        """Busca la mejor fuente y emite la comparación. Devuelve la URL a usar."""
        from hifihub.engine.multisource import find_best_source

        self._bus.emit("download:progress", {
            "job_id": job_id, "status": "searching", "stage": "buscando mejores fuentes…",
        })
        cands = find_best_source(
            url,
            cookies_browser=job.get("cookies_browser", ""),
            cookies_file=job.get("cookies_file", ""),
            progress=lambda msg: self._bus.emit(
                "download:progress", {"job_id": job_id, "status": "searching", "stage": msg}
            ),
        )
        if not cands:
            return url
        self._bus.emit("download:sources", {
            "job_id": job_id,
            "sources": [
                {"source": c.source, "quality": c.quality.label(),
                 "match": c.match_score, "original": c.is_original_input,
                 "chosen": i == 0, "title": c.title}
                for i, c in enumerate(cands)
            ],
        })
        best = cands[0]
        return url if best.is_original_input else best.url

    def start_batch(self, opts: dict[str, Any]) -> dict[str, Any]:
        """Lanza un lote desde un .txt en un hilo. Avance por eventos batch:*."""
        from hifihub.batch.queue import parse_links_file, run_batch
        from hifihub.batch.registry import Registry

        file = opts.get("file")
        if not file or not Path(file).exists():
            return {"ok": False, "error": "Archivo de lote no válido."}
        try:
            urls = parse_links_file(file)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        if not urls:
            return {"ok": False, "error": "El archivo no contiene enlaces."}

        cfg = Config.load()
        dest = Path(opts.get("dir") or cfg.download_dir)
        job = dict(
            profile=opts.get("profile") or cfg.default_profile,
            template=opts.get("template") or "flat",
            enrich=bool(opts.get("enrich", cfg.enrich_library)),
            acoustid_key=cfg.acoustid_api_key,
            replaygain=cfg.apply_replaygain,
            cookies_browser=cfg.cookies_browser,
            cookies_file=cfg.cookies_file,
            player_clients=cfg.youtube_player_clients,
            pacing=cfg.download_pacing,
            concurrency=cfg.batch_concurrency,
            on_event=lambda ch, p: self._bus.emit(ch, p),
            registry=Registry(),
        )
        threading.Thread(
            target=lambda: run_batch(urls, dest, **job), daemon=True
        ).start()
        return {"ok": True, "total": len(urls)}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _progress_payload(job_id: str, d: dict[str, Any]) -> dict[str, Any]:
    status = d.get("status")
    payload: dict[str, Any] = {"job_id": job_id, "status": status}
    if status == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        done = d.get("downloaded_bytes") or 0
        payload["percent"] = round(done / total * 100, 1) if total else None
        payload["speed"] = (d.get("_speed_str") or "").strip() or None
        payload["eta"] = d.get("eta")
        payload["filename"] = d.get("filename")
    elif status == "finished":
        # Fin de la descarga del stream.
        payload["percent"] = 100.0
        payload["stage"] = "postprocesando"
    return payload
