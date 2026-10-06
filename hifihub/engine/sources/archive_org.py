"""Fuente: Internet Archive (archive.org).

Por qué existe este módulo aparte y NO usa yt-dlp
--------------------------------------------------
El extractor `archive.org` de yt-dlp scrapea el HTML del reproductor embebido
(busca un elemento `play-av`) y está roto: archive.org cambió esa página. En vez
de depender de ese scraping, esta fuente habla con las **APIs públicas y
documentadas** de archive.org, que son estables:

- Búsqueda:  `advancedsearch.php`  -> lista de ítems (identifier, title, creator…)
- Ficheros:  `/metadata/<id>`      -> todos los ficheros con formato, duración y tamaño
- Descarga:  `/download/<id>/<fichero>` -> HTTP directo

Consecuencia práctica: si YouTube se rompe, esta fuente sigue; si archive.org
cambia, YouTube sigue. No comparten ninguna dependencia de extracción.

Qué se puede bajar de aquí (y por qué merece la pena)
-----------------------------------------------------
Material que archive.org distribuye legalmente: la **Live Music Archive**
(colección `etree`, conciertos de bandas que autorizan expresamente la grabación
y difusión), audio de dominio público y obra con licencia libre. Muchos ítems
tienen **FLAC** e incluso **24bit Flac**: lossless real, por encima de cualquier
stream lossy (YouTube ~130 kbps Opus, Deezer 320 kbps MP3).

Un "ítem" de archive.org suele ser un concierto completo = varias pistas, así que
se trata como un ÁLBUM: se descargan todas sus pistas eligiendo el mejor formato
disponible de cada una.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hifihub.engine.sources.base import Candidate, Quality, SourceError, matches

NAME = "archive"
LABEL = "archive.org"

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{ident}"
DOWNLOAD_URL = "https://archive.org/download/{ident}/{name}"

USER_AGENT = "HiFiHub/1.0 (+personal music library manager)"
TIMEOUT = 30

# Formato de archive.org -> (codec, es_lossless, prioridad). La prioridad decide
# qué fichero se elige de una misma pista cuando hay varias versiones.
AUDIO_FORMATS: dict[str, tuple[str, bool, int]] = {
    "24bit Flac": ("flac", True, 60),
    "Flac": ("flac", True, 50),
    "Shorten": ("shn", True, 40),
    "AIFF": ("aiff", True, 38),
    "WAVE": ("wav", True, 37),
    "Ogg Vorbis": ("vorbis", False, 20),
    "VBR MP3": ("mp3", False, 15),
    "MP3": ("mp3", False, 14),
    "128Kbps MP3": ("mp3", False, 13),
    "64Kbps MP3": ("mp3", False, 10),
    "32Kbps MP3": ("mp3", False, 8),
}

# Ficheros que archive.org genera como derivados y que NO son la pista.
_SKIP_FORMATS = frozenset({
    "Metadata", "Checksums", "Archive BitTorrent", "Columbia Peaks", "PNG",
    "Spectrogram", "Item Tile", "Flac FingerPrint", "Text", "JPEG", "Unknown",
})


# --- Modelo ------------------------------------------------------------------

@dataclass(frozen=True)
class ArchiveFile:
    """Un fichero de audio concreto dentro de un ítem."""
    name: str
    fmt: str
    codec: str
    lossless: bool
    priority: int
    length: float | None
    size: int | None
    title: str | None
    raw_track: str | None

    @property
    def abr(self) -> float | None:
        """kbps reales calculados de tamaño/duración (archive no los publica)."""
        if not self.size or not self.length:
            return None
        return (self.size * 8) / self.length / 1000.0

    def quality(self) -> Quality:
        return Quality(
            codec=self.codec,
            abr=None if self.lossless else self.abr,
            asr=None,  # archive.org no publica sample rate por fichero
            ext=Path(self.name).suffix.lstrip(".").lower() or self.codec,
            is_lossless=self.lossless,
            filesize=self.size,
        )


@dataclass
class ArchiveItem:
    """Un ítem (habitualmente un concierto completo = un álbum)."""
    identifier: str
    title: str
    creator: str | None = None
    date: str | None = None
    tracks: list[ArchiveFile] = field(default_factory=list)

    @property
    def total_duration(self) -> float | None:
        lens = [t.length for t in self.tracks if t.length]
        return sum(lens) if lens else None

    def best_quality(self) -> Quality:
        if not self.tracks:
            return Quality(codec="?", abr=None, asr=None, ext="?", is_lossless=False)
        return max((t.quality() for t in self.tracks), key=lambda q: q.rank())

    def to_candidate(self, *, match_score: float = 1.0) -> Candidate:
        return Candidate(
            source=NAME,
            ref=self.identifier,
            title=self.title,
            quality=self.best_quality(),
            artist=self.creator,
            album=self.title,
            date=self.date,
            duration=self.total_duration,
            is_album=len(self.tracks) > 1,
            track_count=len(self.tracks),
            match_score=match_score,
            extra={"identifier": self.identifier},
        )


# --- Utilidades puras (testeables sin red) -----------------------------------

def parse_length(value: Any) -> float | None:
    """archive.org publica la duración como "119.68" o como "02:00" / "1:02:00"."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        secs = 0.0
        for n in nums:                 # h:m:s o m:s
            secs = secs * 60 + n
        return secs
    try:
        return float(text)
    except ValueError:
        return None


def identifier_from_url(url: str) -> str | None:
    """Saca el identificador de una URL de archive.org (o None si no lo es)."""
    if not url:
        return None
    m = re.match(
        r"https?://(?:www\.)?archive\.org/(?:details|download|metadata|embed)/([^/?#]+)",
        url.strip(), re.I,
    )
    return urllib.parse.unquote(m.group(1)) if m else None


def is_archive_url(url: str) -> bool:
    return identifier_from_url(url) is not None


def _audio_file(entry: dict[str, Any]) -> ArchiveFile | None:
    """Convierte un fichero del metadata API en ArchiveFile (o None si no es audio)."""
    fmt = (entry.get("format") or "").strip()
    if not fmt or fmt in _SKIP_FORMATS:
        return None
    spec = AUDIO_FORMATS.get(fmt)
    if spec is None:
        return None
    codec, lossless, priority = spec
    size = entry.get("size")
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    return ArchiveFile(
        name=entry.get("name", ""),
        fmt=fmt,
        codec=codec,
        lossless=lossless,
        priority=priority,
        length=parse_length(entry.get("length")),
        size=size,
        title=(entry.get("title") or None),
        raw_track=(str(entry["track"]) if entry.get("track") is not None else None),
    )


def _track_key(f: ArchiveFile) -> str:
    """Agrupa las versiones (FLAC/MP3/…) de una MISMA pista.

    archive.org nombra los derivados con el mismo nombre base y otra extensión,
    así que el nombre sin extensión identifica la pista.
    """
    return Path(f.name).stem.lower()


def select_tracks(files: list[dict[str, Any]]) -> list[ArchiveFile]:
    """De todos los ficheros del ítem, la MEJOR versión de cada pista, en orden.

    Prefiere lossless (24bit Flac > Flac > Shorten) sobre lossy y conserva el
    orden en que archive.org lista los ficheros, que es el orden del concierto.
    """
    best: dict[str, ArchiveFile] = {}
    order: list[str] = []
    for entry in files or []:
        af = _audio_file(entry)
        if af is None or not af.name:
            continue
        key = _track_key(af)
        if key not in best:
            order.append(key)
            best[key] = af
        elif af.priority > best[key].priority:
            best[key] = af
    return [best[k] for k in order]


def parse_metadata(ident: str, payload: dict[str, Any]) -> ArchiveItem:
    """Construye un ArchiveItem del JSON de `/metadata/<id>`."""
    md = payload.get("metadata") or {}
    creator = md.get("creator")
    if isinstance(creator, list):
        creator = creator[0] if creator else None
    return ArchiveItem(
        identifier=ident,
        title=str(md.get("title") or ident),
        creator=str(creator) if creator else None,
        date=_year(md.get("date") or md.get("year")),
        tracks=select_tracks(payload.get("files") or []),
    )


def _year(value: Any) -> str | None:
    if not value:
        return None
    m = re.search(r"(\d{4})", str(value))
    return m.group(1) if m else None


def build_search_query(text: str, *, lossless_only: bool = False,
                       collection: str = "") -> str:
    """Consulta para advancedsearch.php restringida a audio."""
    parts: list[str] = []
    text = (text or "").strip()
    if text:
        parts.append(f"({text})")
    parts.append("mediatype:(audio)")
    if collection:
        parts.append(f"collection:({collection})")
    if lossless_only:
        parts.append('format:("Flac" OR "24bit Flac" OR "Shorten")')
    return " AND ".join(parts)


# --- Capa de red -------------------------------------------------------------

def _get_json(url: str, params: list[tuple[str, Any]] | None = None) -> dict[str, Any]:
    import requests

    try:
        r = requests.get(url, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 — cualquier fallo de red es de la fuente
        raise SourceError(f"archive.org no respondió: {e}") from e


def search_items(query: str, limit: int = 6, *, lossless_only: bool = False,
                 collection: str = "") -> list[dict[str, Any]]:
    """Busca ítems de audio. Devuelve los `docs` crudos de advancedsearch."""
    params: list[tuple[str, Any]] = [
        ("q", build_search_query(query, lossless_only=lossless_only,
                                 collection=collection)),
        ("rows", max(1, int(limit))),
        ("page", 1),
        ("output", "json"),
        ("sort[]", "downloads desc"),
    ]
    for fl in ("identifier", "title", "creator", "year", "date"):
        params.append(("fl[]", fl))
    data = _get_json(SEARCH_URL, params)
    return list((data.get("response") or {}).get("docs") or [])


def fetch_item(ident: str) -> ArchiveItem:
    """Ítem completo con sus pistas ya elegidas (mejor formato de cada una)."""
    payload = _get_json(METADATA_URL.format(ident=urllib.parse.quote(ident)))
    if not payload or not payload.get("files"):
        raise SourceError(f"El ítem «{ident}» no existe o no tiene ficheros.")
    item = parse_metadata(ident, payload)
    if not item.tracks:
        raise SourceError(f"El ítem «{ident}» no contiene audio descargable.")
    return item


def file_url(ident: str, name: str) -> str:
    return DOWNLOAD_URL.format(
        ident=urllib.parse.quote(ident),
        name=urllib.parse.quote(name),
    )


# --- Búsqueda de candidatos --------------------------------------------------

def find_album(query: str, *, limit: int = 5,
               progress: Callable[[str], None] | None = None) -> list[Candidate]:
    """Busca ítems (conciertos/álbumes) que encajen con el texto dado."""
    docs = search_items(query, limit=limit)
    out: list[Candidate] = []
    for doc in docs:
        ident = doc.get("identifier")
        if not ident:
            continue
        if progress:
            progress(f"archive.org: leyendo {ident}…")
        try:
            out.append(fetch_item(ident).to_candidate())
        except SourceError:
            continue  # un ítem ilegible no invalida la búsqueda
    out.sort(key=lambda c: c.rank(), reverse=True)
    return out


def _scan_items_for_track(docs: list[dict[str, Any]], title: str,
                          duration: float | None,
                          progress: Callable[[str], None] | None,
                          seen: set[str]) -> list[Candidate]:
    """Recorre los ficheros de cada ítem buscando una pista que encaje."""
    out: list[Candidate] = []
    for doc in docs:
        ident = doc.get("identifier")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        if progress:
            progress(f"archive.org: buscando «{title}» en {ident}…")
        try:
            item = fetch_item(ident)
        except SourceError:
            continue  # un ítem ilegible no invalida la búsqueda
        for idx, tr in enumerate(item.tracks, start=1):
            cand_title = tr.title or Path(tr.name).stem
            ok, score = matches(duration, title, tr.length, cand_title)
            if not ok:
                continue
            out.append(Candidate(
                source=NAME,
                ref=f"{ident}/{tr.name}",
                title=cand_title,
                quality=tr.quality(),
                artist=item.creator,
                album=item.title,
                track_number=idx,
                date=item.date,
                duration=tr.length,
                match_score=round(score, 3),
                extra={"identifier": ident, "file": tr.name},
            ))
    return out


def find_track(title: str, artist: str = "", duration: float | None = None, *,
               items: int = 4,
               progress: Callable[[str], None] | None = None) -> list[Candidate]:
    """Busca una CANCIÓN concreta dentro de los ítems de archive.org.

    Los ítems son conciertos completos, así que la coincidencia se hace en dos
    niveles: primero se buscan ítems, y después se recorre la lista de ficheros
    de cada uno buscando una pista cuyo título (y duración) encaje.

    Coste acotado a propósito: `items` ítems por barrido, y el segundo barrido
    (solo por artista) se hace ÚNICAMENTE si el primero no encontró nada, para no
    disparar decenas de peticiones en el caso común.
    """
    query = " ".join(p for p in (artist, title) if p).strip()
    if not query:
        return []
    seen: set[str] = set()
    out = _scan_items_for_track(search_items(query, limit=items), title, duration,
                                progress, seen)
    # El título de la canción no aparece en el del ítem ("… Live at X on FECHA"),
    # así que si la búsqueda combinada falla, se reintenta solo con el artista.
    if not out and artist:
        out = _scan_items_for_track(search_items(artist, limit=items), title,
                                    duration, progress, seen)
    out.sort(key=lambda c: c.rank(), reverse=True)
    return out


# --- Descarga ----------------------------------------------------------------

def _safe_name(name: str) -> str:
    """Nombre de fichero válido en Windows conservando la extensión."""
    stem = Path(name).name
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(". ") or "pista"


def download_file(ident: str, name: str, dest_dir: Path, *,
                  progress: Callable[[dict[str, Any]], None] | None = None,
                  expected_size: int | None = None) -> Path:
    """Descarga un fichero del ítem por HTTP. Devuelve la ruta local.

    Emite progreso con las MISMAS claves que yt-dlp para que la UI existente lo
    entienda sin cambios (`status`, `downloaded_bytes`, `total_bytes`).
    """
    import requests

    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / _safe_name(name)
    tmp = out.with_suffix(out.suffix + ".part")
    url = file_url(ident, name)
    try:
        with requests.get(url, stream=True, timeout=TIMEOUT,
                          headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0) or expected_size or 0
            done = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=262144):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress({
                            "status": "downloading",
                            "downloaded_bytes": done,
                            "total_bytes": total or None,
                            "filename": str(out),
                        })
    except SourceError:
        raise
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise SourceError(f"No se pudo descargar «{name}»: {e}") from e

    tmp.replace(out)
    if progress:
        progress({"status": "finished", "filename": str(out),
                  "downloaded_bytes": out.stat().st_size})
    return out


def download_item(ident: str, dest_dir: Path, *,
                  tracks: list[ArchiveFile] | None = None,
                  item: ArchiveItem | None = None,
                  progress: Callable[[dict[str, Any]], None] | None = None,
                  stage: Callable[[str], None] | None = None) -> list[Path]:
    """Descarga todas las pistas de un ítem y les escribe los tags de archive.org.

    Los tags básicos (artista/álbum/título/nº/año) se escriben ANTES de que actúe
    el postprocesado común, para que el enriquecido y la organización por
    plantilla tengan de dónde partir.
    """
    item = item or fetch_item(ident)
    todo = tracks if tracks is not None else item.tracks
    out: list[Path] = []
    for idx, tr in enumerate(todo, start=1):
        if stage:
            stage(f"archive.org: descargando {idx}/{len(todo)} — {tr.title or tr.name}")
        path = download_file(ident, tr.name, dest_dir, progress=progress,
                             expected_size=tr.size)
        _write_basic_tags(path, item, tr, idx)
        out.append(path)
    return out


def _write_basic_tags(path: Path, item: ArchiveItem, tr: ArchiveFile,
                      index: int) -> None:
    """Tags mínimos desde los metadatos de archive.org (tolerante a fallos)."""
    try:
        from hifihub.metadata.tagger import TrackTags, write_tags

        write_tags(path, TrackTags(
            title=(tr.title or Path(tr.name).stem),
            artist=item.creator,
            album=item.title,
            album_artist=item.creator,
            track_number=index,
            date=item.date,
        ))
    except Exception:  # noqa: BLE001 — sin tags igual sirve; el enrich lo arregla
        pass
