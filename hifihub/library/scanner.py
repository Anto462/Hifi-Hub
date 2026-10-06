"""
Escaneo de carpetas hacia la biblioteca SQLite.

Incremental: solo re-lee un archivo si su mtime cambió respecto a lo indexado.
Cachea la portada incrustada como thumbnail (~300px) en disco, para que la UI no
tenga que abrir el archivo de audio entero cada vez que pinta una carátula.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from hifihub import paths
from hifihub.audio import inspect as audio_inspect
from hifihub.library.db import Library, Track
from hifihub.metadata import tagger

AUDIO_EXTS = {".opus", ".flac", ".m4a", ".mp3", ".ogg", ".oga", ".alac", ".aac", ".wav"}
COVER_CACHE = paths.CONFIG_DIR / "covers"
_THUMB_SIZE = 300


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped: int = 0
    errors: int = 0


def iter_audio_files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            yield p


def scan(roots: list[Path] | list[str], lib: Library, prune: bool = True) -> ScanResult:
    result = ScanResult()
    known = lib.all_paths_mtime()
    seen: set[str] = set()

    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for file in iter_audio_files(root):
            key = str(file)
            seen.add(key)
            try:
                mtime = file.stat().st_mtime
                if key in known and known[key] and abs(known[key] - mtime) < 1e-6:
                    result.skipped += 1
                    continue
                track = _build_track(file, mtime)
                is_new = key not in known
                lib.upsert_track(track)
                result.added += is_new
                result.updated += not is_new
            except Exception:  # Si hay un archivo corrupto no aborta el escaneo
                result.errors += 1

    if prune:
        result.removed = lib.delete_missing(seen)
    return result


def _build_track(file: Path, mtime: float) -> Track:
    tags = tagger.read_common_tags(file)
    try:
        info = audio_inspect.probe(file)
    except audio_inspect.ProbeError:
        info = None

    cover_path = _cache_cover(file)
    return Track(
        path=str(file),
        title=tags.title or file.stem,
        artist=tags.artist,
        album=tags.album,
        album_artist=tags.album_artist or tags.artist,
        genre=tags.genre,
        date=tags.date,
        track_number=tags.track_number,
        duration=info.duration if info else None,
        codec=info.codec if info else None,
        sample_rate=info.sample_rate if info else None,
        bit_depth=info.bit_depth if info else None,
        bit_rate=info.bit_rate if info else None,
        lossless=info.lossless if info else False,
        cover_path=cover_path,
        mtime=mtime,
    )


def _cache_cover(file: Path) -> str | None:
    try:
        raw = tagger.extract_cover(file)
    except Exception:  
        raw = None
    if not raw:
        return None

    COVER_CACHE.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(raw).hexdigest()[:16]
    out = COVER_CACHE / f"{digest}.jpg"
    if out.exists():
        return str(out)
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((_THUMB_SIZE, _THUMB_SIZE), Image.LANCZOS)
        img.convert("RGB").save(out, format="JPEG", quality=85)
        return str(out)
    except Exception:  
        return None
