"""Sincronizar con Last.fm.

Registra las reproducciones en la cuenta de Last.fm del usuario. Requiere una
app registrada (api key + secret) y una session key obtenida tras autorizar. Sin
credenciales, el scrobbler es un no-op silencioso: la estadística de escucha
local (SQLite) sigue funcionando igual.

Regla de Last.fm: se hace "scrobble" cuando la pista lleva sonando >50% o >4 min.
El caller decide cuándo llamar; aquí solo se firma y envía la petición. En general esto es una funcionalidad extra para el registro externo.

Conexión desde Ajustes (flujo oficial de escritorio de Last.fm): pedir un token,
que el usuario lo autorice en su navegador y canjearlo por la session key.
"""

from __future__ import annotations

import hashlib
import threading
import time

import requests

API_ROOT = "https://ws.audioscrobbler.com/2.0/"
AUTH_PAGE = "https://www.last.fm/api/auth/"
_TIMEOUT = 10


class LastFmError(RuntimeError):
    """Fallo al conectar la cuenta (credenciales, token sin autorizar, red)."""


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


class BackgroundScrobbler(LastFmScrobbler):
    """Igual que LastFmScrobbler, pero envía en un hilo aparte.

    El controller avisa desde el hilo de eventos de mpv: una petición síncrona
    (hasta 10 s si Last.fm no responde) congelaría los eventos de reproducción
    (posición, cambio de pista) mientras tanto. Aquí la llamada vuelve al
    instante y el resultado de red no afecta a la reproducción.
    """

    def scrobble(self, artist: str, track: str, album: str | None = None,
                 timestamp: int | None = None) -> bool:
        if not self.enabled or not artist or not track:
            return False
        # La marca de tiempo se fija AHORA, no cuando el hilo llegue a enviar.
        ts = timestamp or int(time.time())
        _background(super().scrobble, artist, track, album, ts)
        return True

    def update_now_playing(self, artist: str, track: str, album: str | None = None) -> bool:
        if not self.enabled or not artist or not track:
            return False
        _background(super().update_now_playing, artist, track, album)
        return True


def _background(fn, *args) -> None:
    # No daemon a propósito: al cerrar la app, el último scrobble se termina de
    # enviar (con la ventana ya cerrada) en vez de cortarse a medias. Cada envío
    # dura como mucho _TIMEOUT segundos.
    threading.Thread(target=fn, args=args, daemon=False).start()


# --- Conexión de la cuenta auth y uso de la api ---------------------------------

def _call(method: str, api_key: str, api_secret: str, **extra: str) -> dict:
    """Llamada firmada a la API. Lanza LastFmError con el mensaje de Last.fm."""
    if not api_key or not api_secret:
        raise LastFmError("Faltan la API key y el secreto de tu app de Last.fm.")
    signer = LastFmScrobbler(api_key, api_secret, "")
    params = signer._signed_params({"method": method, "api_key": api_key, **extra})
    try:
        resp = requests.get(API_ROOT, params=params, timeout=_TIMEOUT)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise LastFmError(f"No se pudo contactar con Last.fm: {e}") from e
    if "error" in data:
        raise LastFmError(data.get("message") or f"Error {data['error']} de Last.fm.")
    return data


def get_token(api_key: str, api_secret: str) -> str:
    """Token de un solo uso que el usuario autoriza en el navegador."""
    return _call("auth.getToken", api_key, api_secret)["token"]


def auth_url(api_key: str, token: str) -> str:
    """Página de Last.fm donde el usuario concede el permiso a SU app."""
    return f"{AUTH_PAGE}?api_key={api_key}&token={token}"


def get_session(api_key: str, api_secret: str, token: str) -> tuple[str, str]:
    """Canjea el token ya autorizado por la session key y nombre de usuario.

    La session key no caduca por lo que basta con conectarlo una vez."""
    data = _call("auth.getSession", api_key, api_secret, token=token)
    session = data.get("session") or {}
    if not session.get("key"):
        raise LastFmError("Last.fm no devolvió la sesión.")
    return session["key"], session.get("name", "")
