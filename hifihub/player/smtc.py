"""Integración con los System Media Transport Controls de Windows.

Permite que las teclas multimedia del teclado (play/pausa/siguiente/
anterior) controlen HiFi Hub, y el OVERLAY nativo de Windows 11 (título, artista
y portada en el flyout de volumen).

Para ello crea un `MediaPlayer` de winsdk usado SOLO como sesión de medios del
sistema — no reproduce nada. De forma que solo expone su SMTC, incluso si fallara por alguna update o similar no afecta la reproduccion es mas por integracion.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class MediaControls:
    def __init__(self, callbacks: dict[str, Callable[[], None]]) -> None:
        self._cb = callbacks
        self.available = False
        self._player: Any = None
        self._smtc: Any = None
        self._token: Any = None
        try:
            self._setup()
            self.available = True
        except Exception:  # sin SMTC la reproducción sigue igual
            self.available = False

    def _setup(self) -> None:
        from winsdk.windows.media import (
            MediaPlaybackType,
            SystemMediaTransportControlsButton as Btn,
        )
        from winsdk.windows.media.playback import MediaPlayer

        self._Btn = Btn
        self._MediaPlaybackType = MediaPlaybackType

        self._player = MediaPlayer()
        smtc = self._player.system_media_transport_controls
        smtc.is_enabled = True
        smtc.is_play_enabled = True
        smtc.is_pause_enabled = True
        smtc.is_next_enabled = True
        smtc.is_previous_enabled = True
        self._token = smtc.add_button_pressed(self._on_button)
        self._smtc = smtc

    def _on_button(self, sender: Any, args: Any) -> None:
        btn = args.button
        mapping = {
            self._Btn.PLAY: "play",
            self._Btn.PAUSE: "pause",
            self._Btn.NEXT: "next",
            self._Btn.PREVIOUS: "prev",
        }
        action = mapping.get(btn)
        cb = self._cb.get(action) if action else None
        if cb:
            try:
                cb()
            except Exception:  # error
                pass

    def update_now_playing(
        self,
        title: str | None,
        artist: str | None,
        album: str | None = None,
        cover_path: str | None = None,
    ) -> None:
        if not self.available:
            return
        try:
            updater = self._smtc.display_updater
            updater.type = self._MediaPlaybackType.MUSIC
            props = updater.music_properties
            props.title = title or ""
            props.artist = artist or ""
            if album:
                props.album_title = album
            if cover_path and Path(cover_path).exists():
                self._set_thumbnail(updater, cover_path)
            updater.update()
        except Exception:  # error
            pass

    def _set_thumbnail(self, updater: Any, cover_path: str) -> None:
        from winsdk.windows.foundation import Uri
        from winsdk.windows.storage.streams import RandomAccessStreamReference

        uri = Uri(Path(cover_path).absolute().as_uri())
        updater.thumbnail = RandomAccessStreamReference.create_from_uri(uri)

    def set_playing(self, playing: bool) -> None:
        if not self.available:
            return
        try:
            from winsdk.windows.media import MediaPlaybackStatus

            self._smtc.playback_status = (
                MediaPlaybackStatus.PLAYING if playing else MediaPlaybackStatus.PAUSED
            )
        except Exception:  # error
            pass

    def close(self) -> None:
        try:
            if self._player is not None:
                self._player.close()
        except Exception:  # error
            pass
