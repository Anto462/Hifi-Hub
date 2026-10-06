"""Fuente: Spotify — RESOLUTOR DE METADATOS, no de audio.

Qué hace y qué NO hace
----------------------
Esta lane **nunca descarga audio de Spotify**, y no es una limitación que se pueda
"arreglar": el audio de Spotify va cifrado con DRM (Widevine), así que bajarlo
exige circunvención, que queda fuera del proyecto por diseño. Lo mismo aplica a
Deezer (Blowfish) y a los forks de deemix.

Lo que sí es legítimo y útil: usar Spotify como **catálogo**. Se toma un enlace de
Spotify, se resuelve QUÉ canciones son (artista, título, duración, álbum) y luego
el audio se busca y se descarga desde las fuentes reales del proyecto
(YouTube / archive.org), con el postprocesado normal. Es el mismo enfoque que usan
los "spotify-downloader" honestos: Spotify aporta la lista, no los bytes.

De dónde salen los metadatos
----------------------------
1. **Web API oficial** (recomendado): requiere que el usuario cree su propia app
   gratuita en developer.spotify.com y pegue su Client ID / Client Secret en
   Ajustes. Da metadatos completos y listas de álbum/playlist. Flujo Client
   Credentials, sin login de usuario.
2. **oEmbed público** (respaldo, sin credenciales): endpoint público de Spotify.
   Devuelve solo el TÍTULO (comprobado: `author_name` viene vacío), así que sirve
   para una pista sola y no puede listar álbumes ni playlists.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any

from hifihub.engine.sources.base import SourceError

NAME = "spotify"
LABEL = "Spotify (metadatos)"

OEMBED_URL = "https://open.spotify.com/oembed"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
TIMEOUT = 25
USER_AGENT = "HiFiHub/1.0 (+personal music library manager)"


@dataclass(frozen=True)
class WantedTrack:
    """Una canción que el usuario quiere, todavía SIN fuente de audio asignada."""
    title: str
    artist: str | None = None
    album: str | None = None
    track_number: int | None = None
    date: str | None = None
    duration: float | None = None

    @property
    def query(self) -> str:
        return " ".join(p for p in (self.artist, self.title) if p).strip()


# --- URLs (puro) -------------------------------------------------------------

_URL_RE = re.compile(
    r"(?:https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?|spotify:)"
    r"(track|album|playlist)[/:]([A-Za-z0-9]+)",
    re.I,
)


def parse_url(url: str) -> tuple[str, str] | None:
    """('track'|'album'|'playlist', id) o None si no es un enlace de Spotify."""
    m = _URL_RE.search((url or "").strip())
    return (m.group(1).lower(), m.group(2)) if m else None


def is_spotify_url(url: str) -> bool:
    return parse_url(url) is not None


def _year(value: Any) -> str | None:
    m = re.search(r"(\d{4})", str(value or ""))
    return m.group(1) if m else None


def _artists(node: dict[str, Any]) -> str | None:
    names = [a.get("name") for a in (node.get("artists") or []) if a.get("name")]
    return ", ".join(names) if names else None


def track_from_api(node: dict[str, Any], *, album: str | None = None,
                   date: str | None = None) -> WantedTrack:
    """Convierte un objeto `track` de la Web API en WantedTrack."""
    album_node = node.get("album") or {}
    ms = node.get("duration_ms")
    return WantedTrack(
        title=str(node.get("name") or "").strip(),
        artist=_artists(node),
        album=album or album_node.get("name"),
        track_number=node.get("track_number"),
        date=date or _year(album_node.get("release_date")),
        duration=(float(ms) / 1000.0) if ms else None,
    )


# --- Web API oficial ---------------------------------------------------------

def _token(client_id: str, client_secret: str) -> str:
    import requests

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        r = requests.post(
            TOKEN_URL, timeout=TIMEOUT,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        tok = r.json().get("access_token")
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"Spotify rechazó las credenciales: {e}") from e
    if not tok:
        raise SourceError("Spotify no devolvió token de acceso.")
    return str(tok)


def _api(path: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    import requests

    try:
        r = requests.get(f"{API_BASE}{path}", timeout=TIMEOUT, params=params,
                         headers={"Authorization": f"Bearer {token}",
                                  "User-Agent": USER_AGENT})
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"Spotify API falló en {path}: {e}") from e


def _resolve_with_api(kind: str, sid: str, client_id: str,
                      client_secret: str) -> list[WantedTrack]:
    token = _token(client_id, client_secret)

    if kind == "track":
        return [track_from_api(_api(f"/tracks/{sid}", token))]

    if kind == "album":
        alb = _api(f"/albums/{sid}", token)
        album_name, date = alb.get("name"), _year(alb.get("release_date"))
        out = [track_from_api({**t, "album": alb}, album=album_name, date=date)
               for t in (alb.get("tracks") or {}).get("items") or []]
        # Paginación del álbum (más de 50 pistas).
        nxt = ((alb.get("tracks") or {}).get("next"))
        offset = 50
        while nxt:
            page = _api(f"/albums/{sid}/tracks", token, {"limit": 50, "offset": offset})
            out += [track_from_api({**t, "album": alb}, album=album_name, date=date)
                    for t in page.get("items") or []]
            nxt, offset = page.get("next"), offset + 50
        return out

    # playlist
    out, offset = [], 0
    while True:
        page = _api(f"/playlists/{sid}/tracks", token, {"limit": 100, "offset": offset})
        items = page.get("items") or []
        for it in items:
            node = it.get("track") or {}
            if node.get("type") == "track" and node.get("name"):
                out.append(track_from_api(node))
        if not page.get("next"):
            break
        offset += 100
    return out


# --- Respaldo sin credenciales (oEmbed) --------------------------------------

def _resolve_with_oembed(kind: str, url: str) -> list[WantedTrack]:
    import requests

    if kind != "track":
        raise SourceError(
            "Para álbumes y playlists de Spotify hacen falta tus credenciales de "
            "la Web API (Ajustes → Spotify). El endpoint público solo identifica "
            "pistas sueltas."
        )
    try:
        r = requests.get(OEMBED_URL, params={"url": url}, timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        title = (r.json() or {}).get("title")
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"Spotify no identificó el enlace: {e}") from e
    if not title:
        raise SourceError("Spotify no devolvió el título de la pista.")
    # Sin artista: oEmbed no lo expone. La búsqueda irá solo por título.
    return [WantedTrack(title=str(title).strip())]


# --- Entrada pública ---------------------------------------------------------

def resolve(url: str, *, client_id: str = "", client_secret: str = "") -> list[WantedTrack]:
    """Enlace de Spotify -> lista de canciones a buscar en OTRAS fuentes.

    No descarga nada de Spotify: solo dice qué canciones son.
    """
    parsed = parse_url(url)
    if parsed is None:
        raise SourceError("No parece un enlace de Spotify.")
    kind, sid = parsed
    if client_id and client_secret:
        return _resolve_with_api(kind, sid, client_id, client_secret)
    return _resolve_with_oembed(kind, url)
