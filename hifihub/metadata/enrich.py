"""Enriquecimiento de metadatos tras la descarga.

  1. cleaner  -> artista/título limpios.
  2. AcoustID -> huella acústica -> identificación canónica (requiere clave gratis)
  3. MusicBrainz -> álbum, año, nº de pista desde el MBID (requiere identificación)
  4. Cover Art Archive -> portada real del álbum o miniatura de YouTube
  5. LRCLIB   -> letras (sincronizadas o planas).

Si la confianza de AcoustID es baja, se marca `verified=False`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import requests

from hifihub import paths
from hifihub.metadata import artwork, cleaner
from hifihub.metadata.tagger import TrackTags, write_tags

ACOUSTID_THRESHOLD = 0.5
LRCLIB_BASE = "https://lrclib.net/api/get"
CAA_RELEASE_GROUP = "https://coverartarchive.org/release-group/{mbid}/front-500"
USER_AGENT = "HiFiHub/0.1 (https://github.com/local/hifihub)"
_TIMEOUT = 15


@dataclass
class EnrichResult:
    tags: TrackTags
    identified: bool = False
    verified: bool = False
    acoustid_score: float | None = None
    cover_source: str | None = None    # 'coverart' | 'thumbnail' | None
    lyrics_found: bool = False
    notes: list[str] = field(default_factory=list)


def enrich_file(
    path: Path | str,
    *,
    raw_title: str,
    uploader: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    date: str | None = None,
    track_number: int | None = None,
    duration: float | None = None,
    thumbnail_url: str | None = None,
    acoustid_key: str = "",
    embed_cover: bool = True,
    analyze_bpm_key: bool = False,
) -> EnrichResult:
    path = Path(path)

    # 1. Base offline: limpiar título/artista.
    parsed = cleaner.parse(raw_title, uploader=uploader, artist=artist)
    tags = TrackTags(
        title=parsed.title,
        artist=parsed.artist,
        album=album,
        album_artist=parsed.artist,
        date=date,
        track_number=track_number,
    )
    result = EnrichResult(tags=tags)

    # 2. Identificación por huella acústica (opcional).
    release_group_id = None
    if acoustid_key:
        match = _identify_acoustid(path, acoustid_key, duration)
        if match:
            result.identified = True
            result.acoustid_score = match["score"]
            result.verified = match["score"] >= ACOUSTID_THRESHOLD
            if result.verified:
                tags.title = match.get("title") or tags.title
                tags.artist = match.get("artist") or tags.artist
                tags.album_artist = tags.artist
                tags.musicbrainz_recordingid = match.get("recording_id")
                release_group_id = match.get("release_group_id")
                # 3. Detalles desde MusicBrainz (álbum/año/pista).
                _apply_musicbrainz(tags, match.get("recording_id"), result)
            else:
                result.notes.append(f"AcoustID de baja confianza ({match['score']:.2f}); sin verificar.")
        else:
            result.notes.append("AcoustID no encontró coincidencias.")
    else:
        result.notes.append("Sin clave AcoustID: identificación omitida.")

    # 4. Portada: Cover Art Archive del álbum, si no la miniatura.
    if embed_cover:
        _apply_cover(path, release_group_id, thumbnail_url, result)

    # 5. Letras (LRCLIB).
    if tags.artist and tags.title:
        lyrics = _fetch_lyrics(tags.artist, tags.title, tags.album, duration)
        if lyrics:
            tags.lyrics = lyrics
            result.lyrics_found = True

    # Escribir todo al archivo.
    try:
        write_tags(path, tags)
    except Exception as e:  # noqa: BLE001
        result.notes.append(f"No se pudieron escribir tags: {e}")

    # BPM/tonalidad (opcional, costoso): tras escribir el resto para no perderlo.
    if analyze_bpm_key:
        try:
            from hifihub.audio.analysis import analyze_and_tag

            music = analyze_and_tag(path)
            result.notes.append(f"BPM {music.bpm}, tonalidad {music.key}.")
        except Exception as e:  # noqa: BLE001
            result.notes.append(f"Análisis BPM/tonalidad falló: {e}")

    return result


# --- AcoustID ---------------------------------------------------------------

def _identify_acoustid(path: Path, key: str, duration: float | None) -> dict | None:
    try:
        import acoustid
    except ImportError:
        return None
    # pyacoustid localiza fpcalc por la variable de entorno FPCALC.
    os.environ.setdefault("FPCALC", str(paths.fpcalc_exe()))
    try:
        dur, fp = acoustid.fingerprint_file(str(path))
        data = acoustid.lookup(key, fp, int(dur), meta="recordings releasegroups")
    except acoustid.AcoustidError:
        return None
    except Exception:  # noqa: BLE001 — red/fpcalc; degradar sin romper
        return None

    results = data.get("results") or []
    if not results:
        return None
    best = max(results, key=lambda r: r.get("score", 0))
    recordings = best.get("recordings") or []
    rec = recordings[0] if recordings else {}
    artists = rec.get("artists") or []
    rgs = rec.get("releasegroups") or []
    return {
        "score": float(best.get("score", 0)),
        "recording_id": rec.get("id"),
        "title": rec.get("title"),
        "artist": artists[0]["name"] if artists else None,
        "release_group_id": rgs[0]["id"] if rgs else None,
        "album": rgs[0].get("title") if rgs else None,
    }


# --- MusicBrainz ------------------------------------------------------------

def _apply_musicbrainz(tags: TrackTags, recording_id: str | None, result: EnrichResult) -> None:
    if not recording_id:
        return
    try:
        import musicbrainzngs
    except ImportError:
        return
    try:
        musicbrainzngs.set_useragent("HiFiHub", "0.1", "https://github.com/local/hifihub")
        data = musicbrainzngs.get_recording_by_id(recording_id, includes=["releases"])
        rec = data.get("recording", {})
        releases = rec.get("release-list") or []
        if releases:
            rel = releases[0]
            tags.album = tags.album or rel.get("title")
            tags.musicbrainz_albumid = rel.get("id")
            if rel.get("date"):
                tags.date = tags.date or rel["date"][:4]
    except Exception as e:  # noqa: BLE001
        result.notes.append(f"MusicBrainz no disponible: {e}")


# --- Cover Art --------------------------------------------------------------

def _apply_cover(path: Path, release_group_id, thumbnail_url, result: EnrichResult) -> None:
    if release_group_id:
        url = CAA_RELEASE_GROUP.format(mbid=release_group_id)
        try:
            resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": USER_AGENT})
            if resp.ok and resp.content:
                artwork.embed_from_bytes(path, resp.content)
                result.cover_source = "coverart"
                return
        except (requests.RequestException, OSError):
            pass
    if thumbnail_url and artwork.embed_from_url(path, thumbnail_url):
        result.cover_source = "thumbnail"


# --- LRCLIB -----------------------------------------------------------------

def _fetch_lyrics(artist: str, title: str, album: str | None, duration: float | None) -> str | None:
    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = int(duration)
    try:
        resp = requests.get(
            LRCLIB_BASE, params=params, timeout=_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        if not resp.ok:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    # Preferir letra sincronizada (.lrc) sobre la plana.
    return data.get("syncedLyrics") or data.get("plainLyrics") or None
