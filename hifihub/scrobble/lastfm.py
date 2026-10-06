"""Scrobbling opcional a Last.fm.

Registra las reproducciones en la cuenta de Last.fm del usuario. Requiere una
app registrada (api key + secret) y una session key obtenida tras autorizar. Sin
credenciales, el scrobbler es un no-op silencioso: la estadística de escucha
local (SQLite) sigue funcionando igual.

Regla de Last.fm: se hace "scrobble" cuando la pista lleva sonando >50% o >4 min.
El caller decide cuándo llamar; aquí solo se firma y envía la petición. En general esto es una funcionalidad extra para el registro externo. 

Recordar agregar esto al UI XD.
"""

from __future__ import annotations

import hashlib
import time

import requests

API_ROOT = "https://ws.audioscrobbler.com/2.0/"
_TIMEOUT = 10


class LastFmScrobbler:
    def __init__(self, api_key: str, api_secret: str, session_key: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_key = session_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_secret and self.session_key)

    def _signed_params(self, params: dict[str, str]) -> dict[str, str]:
        # Firma api_sig: concatenar claves ordenadas + secret, md5.
        sig_base = "".join(f"{k}{params[k]}" for k in sorted(params))
        sig = hashlib.md5((sig_base + self.api_secret).encode("utf-8")).hexdigest()
        return {**params, "api_sig": sig, "format": "json"}

    def scrobble(self, artist: str, track: str, album: str | None = None,
                 timestamp: int | None = None) -> bool:
        if not self.enabled or not artist or not track:
            return False
        params = {
            "method": "track.scrobble",
            "artist": artist,
            "track": track,
            "timestamp": str(timestamp or int(time.time())),
            "api_key": self.api_key,
            "sk": self.session_key,
        }
        if album:
            params["album"] = album
        try:
            resp = requests.post(API_ROOT, data=self._signed_params(params), timeout=_TIMEOUT)
            return resp.ok
        except requests.RequestException:
            return False

    def update_now_playing(self, artist: str, track: str, album: str | None = None) -> bool:
        if not self.enabled or not artist or not track:
            return False
        params = {
            "method": "track.updateNowPlaying",
            "artist": artist,
            "track": track,
            "api_key": self.api_key,
            "sk": self.session_key,
        }
        if album:
            params["album"] = album
        try:
            resp = requests.post(API_ROOT, data=self._signed_params(params), timeout=_TIMEOUT)
            return resp.ok
        except requests.RequestException:
            return False
