"""Importación de música propia (FLAC, rips de CD, compras) a la biblioteca.

Procesa archivos locales por la MISMA tubería que las descargas, pero con el
ARCHIVO como origen (sin yt-dlp): organiza por plantilla desde sus tags, enriquece
(identifica por huella acústica + portada + letras), aplica ReplayGain y lo añade a
la biblioteca.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hifihub import paths
from hifihub.library import scanner
from hifihub.library.db import Library
from hifihub.metadata import tagger

ImportEventCallback = Callable[[str, dict[str, Any]], None]

AUDIO_EXTS = scanner.AUDIO_EXTS
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class ImportReport:
    imported: int = 0
    skipped: int = 0
    errors: int = 0


def _san(name: str | None, fallback: str) -> str:
    name = (name or "").strip()
    if not name:
        return fallback
    name = _INVALID.sub("_", name).strip(". ")
    return name[:120] or fallback


def dest_path(dest_dir: Path | str, tags: tagger.TrackTags, ext: str) -> Path:
    """Ruta destino Artista/Álbum/NN - Título.ext desde los tags."""
    artist = _san(tags.album_artist or tags.artist, "Sin artista")
    album = _san(tags.album, "Sin álbum")
    title = _san(tags.title, "Sin título")
    if tags.track_number:
        fname = f"{int(tags.track_number):02d} - {title}{ext}"
    else:
        fname = f"{title}{ext}"
    return Path(dest_dir) / artist / album / fname


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 2
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def _ffmpeg_to_flac(src: Path, dst: Path) -> None:
    cmd = [str(paths.ffmpeg_exe()), "-v", "error", "-y", "-i", str(src), "-c:a", "flac", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"conversión a FLAC falló: {r.stderr.strip()[:200]}")


def collect_audio(inputs: list[str] | list[Path]) -> list[Path]:
    """Expande archivos sueltos y carpetas (recursivo) a lista de audios."""
    files: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files.extend(scanner.iter_audio_files(p))
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            files.append(p)
    return files


def import_files(
    inputs: list[str] | list[Path],
    dest_dir: Path | str,
    *,
    move: bool = False,
    enrich: bool = True,
    replaygain: bool = True,
    convert_flac: bool = False,
    acoustid_key: str = "",
    analyze_bpm_key: bool = False,
    lib: Library | None = None,
    on_event: ImportEventCallback | None = None,
) -> ImportReport:
    files = collect_audio(inputs)
    report = ImportReport()
    own_lib = lib is None
    lib = lib or Library()
    total = len(files)

    def emit(ch: str, payload: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(ch, payload)

    done_dsts: list[Path] = []
    try:
        for i, src in enumerate(files):
            src = Path(src)
            try:
                tags = tagger.read_common_tags(src)
                ext = ".flac" if convert_flac else src.suffix.lower()
                dst = dest_path(dest_dir, tags, ext)

                # Si ya está en su sitio exacto, no re-copiar (idempotente).
                if dst.exists() and dst.resolve() == src.resolve():
                    report.skipped += 1
                    emit("import:item", {"src": str(src), "dst": str(dst), "ok": True,
                                         "skipped": True, "done": i + 1, "total": total})
                    continue
                dst = _unique(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)

                # Copiar / mover / convertir (el original solo se borra si se mueve).
                if convert_flac and src.suffix.lower() != ".flac":
                    _ffmpeg_to_flac(src, dst)
                    if move:
                        src.unlink(missing_ok=True)
                elif move:
                    shutil.move(str(src), str(dst))
                else:
                    shutil.copy2(str(src), str(dst))

                # Enriquecer la COPIA en el destino.
                # Misma cadena y opciones que la descarga (incl. BPM/tonalidad).
                if enrich:
                    try:
                        from hifihub.metadata.enrich import enrich_file
                        enrich_file(
                            dst, raw_title=tags.title or dst.stem, artist=tags.artist,
                            album=tags.album, date=tags.date, track_number=tags.track_number,
                            thumbnail_url=None, acoustid_key=acoustid_key,
                            analyze_bpm_key=analyze_bpm_key,
                        )
                    except Exception:  # Si no se puede enriquecer pass, no es requisito al 100
                        pass

                done_dsts.append(dst)
                emit("import:item", {"src": str(src), "dst": str(dst), "ok": True,
                                     "done": i + 1, "total": total})
            except Exception as e:  # noqa: BLE001 — un archivo malo no aborta el lote
                report.errors += 1
                emit("import:item", {"src": str(src), "ok": False, "error": str(e),
                                     "done": i + 1, "total": total})

        # ReplayGain agrupado por álbum
        if replaygain and done_dsts:
            try:
                from hifihub.audio.loudness import LoudnessError, scan_files
                by_album: dict[Path, list[Path]] = {}
                for d in done_dsts:
                    by_album.setdefault(d.parent, []).append(d)
                for group in by_album.values():
                    try:
                        scan_files(group, album=len(group) > 1)
                    except (LoudnessError, Exception):  # noqa: BLE001
                        pass
            except Exception:  # es un extra se puede pass
                pass

        for d in done_dsts:
            try:
                lib.upsert_track(scanner._build_track(d, d.stat().st_mtime))
                report.imported += 1
            except Exception: 
                report.errors += 1

        emit("import:complete", {"imported": report.imported, "skipped": report.skipped,
                                 "errors": report.errors})
    finally:
        if own_lib:
            lib.close()
    return report
