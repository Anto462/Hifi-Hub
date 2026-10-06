"""Escritura/lectura de metadatos con mutagen, abstrayendo el contenedor.

Cada formato guarda los tags de forma distinta:
- Vorbis comments  -> Opus, FLAC, OGG
- MP4 atoms        -> M4A, ALAC, AAC
- ID3v2.4          -> MP3

`write_tags` y `embed_cover` reciben datos neutrales y los traducen al esquema
correcto. Así el resto del código (enrich.py) no sabe de formatos.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, fields
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, USLT
from mutagen.id3._util import ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

VORBIS_EXTS = {".flac", ".opus", ".ogg", ".oga"}
MP4_EXTS = {".m4a", ".mp4", ".alac", ".aac", ".m4b"}
MP3_EXTS = {".mp3"}


@dataclass
class TrackTags:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    date: str | None = None            # año o fecha ISO
    track_number: int | None = None
    total_tracks: int | None = None
    genre: str | None = None
    lyrics: str | None = None
    musicbrainz_recordingid: str | None = None
    musicbrainz_albumid: str | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self))


class TaggerError(RuntimeError):
    pass


def write_tags(path: Path | str, tags: TrackTags) -> None:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in VORBIS_EXTS:
        _write_vorbis(path, tags)
    elif ext in MP4_EXTS:
        _write_mp4(path, tags)
    elif ext in MP3_EXTS:
        _write_id3(path, tags)
    else:
        raise TaggerError(f"Formato no soportado para tagging: {ext}")


def embed_cover(path: Path | str, jpeg: bytes) -> None:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".flac":
        audio = FLAC(str(path))
        audio.clear_pictures()
        audio.add_picture(_flac_picture(jpeg))
        audio.save()
    elif ext in {".opus", ".ogg", ".oga"}:
        audio = OggOpus(str(path)) if ext == ".opus" else OggVorbis(str(path))
        audio["metadata_block_picture"] = [
            base64.b64encode(_flac_picture(jpeg).write()).decode("ascii")
        ]
        audio.save()
    elif ext in MP4_EXTS:
        audio = MP4(str(path))
        audio["covr"] = [MP4Cover(jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
    elif ext in MP3_EXTS:
        audio = _load_id3(path)
        audio.delall("APIC")
        audio.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=jpeg))
        audio.save()
    else:
        raise TaggerError(f"Formato no soportado para portada: {ext}")


def read_tags(path: Path | str) -> dict[str, str]:
    """Lectura ligera y uniforme (para tests y el futuro scanner de biblioteca)."""
    from mutagen import File as MutagenFile

    audio = MutagenFile(str(path))
    if audio is None or audio.tags is None:
        return {}
    out: dict[str, str] = {}
    for key, value in audio.tags.items():
        out[key.lower()] = str(value[0]) if isinstance(value, list) and value else str(value)
    return out


def read_common_tags(path: Path | str) -> TrackTags:
    """Lee tags a un `TrackTags` normalizado, traduciendo desde cada contenedor."""
    ext = Path(path).suffix.lower()
    if ext in MP4_EXTS:
        return _read_mp4(path)
    if ext in MP3_EXTS:
        return _read_id3(path)
    return _read_vorbis(path)  # FLAC/Opus/OGG y por defecto


def _first(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value)


def _split_track(value) -> tuple[int | None, int | None]:
    s = _first(value)
    if not s:
        return None, None
    s = s.split("/")
    try:
        num = int(s[0])
    except (ValueError, IndexError):
        return None, None
    total = int(s[1]) if len(s) > 1 and s[1].isdigit() else None
    return num, total


def _read_vorbis(path) -> TrackTags:
    from mutagen import File as MutagenFile

    audio = MutagenFile(str(path))
    tags = audio.tags or {}
    num, total = _split_track(tags.get("tracknumber"))
    if total is None:
        total = _first(tags.get("tracktotal"))
        total = int(total) if total and str(total).isdigit() else None
    return TrackTags(
        title=_first(tags.get("title")),
        artist=_first(tags.get("artist")),
        album=_first(tags.get("album")),
        album_artist=_first(tags.get("albumartist")),
        genre=_first(tags.get("genre")),
        date=_first(tags.get("date")),
        track_number=num,
        total_tracks=total,
        lyrics=_first(tags.get("lyrics")),
    )


def _read_mp4(path) -> TrackTags:
    audio = MP4(str(path))
    tags = audio.tags or {}
    trkn = tags.get("trkn")
    num = total = None
    if trkn:
        num, total = trkn[0][0] or None, trkn[0][1] or None
    return TrackTags(
        title=_first(tags.get("\xa9nam")),
        artist=_first(tags.get("\xa9ART")),
        album=_first(tags.get("\xa9alb")),
        album_artist=_first(tags.get("aART")),
        genre=_first(tags.get("\xa9gen")),
        date=_first(tags.get("\xa9day")),
        track_number=num,
        total_tracks=total,
        lyrics=_first(tags.get("\xa9lyr")),
    )


def _read_id3(path) -> TrackTags:
    try:
        audio = ID3(str(path))
    except ID3NoHeaderError:
        return TrackTags()
    num, total = _split_track(audio.get("TRCK").text[0] if audio.get("TRCK") else None)
    return TrackTags(
        title=_first(audio.get("TIT2").text if audio.get("TIT2") else None),
        artist=_first(audio.get("TPE1").text if audio.get("TPE1") else None),
        album=_first(audio.get("TALB").text if audio.get("TALB") else None),
        album_artist=_first(audio.get("TPE2").text if audio.get("TPE2") else None),
        genre=_first(audio.get("TCON").text if audio.get("TCON") else None),
        date=_first(str(audio.get("TDRC").text[0]) if audio.get("TDRC") else None),
        track_number=num,
        total_tracks=total,
        lyrics=_read_id3_lyrics(audio),
    )


def _read_id3_lyrics(audio) -> str | None:
    frames = audio.getall("USLT")
    return frames[0].text if frames else None


def extract_cover(path: Path | str) -> bytes | None:
    """Devuelve los bytes de la portada incrustada, si la hay."""
    import base64

    from mutagen import File as MutagenFile

    ext = Path(path).suffix.lower()
    audio = MutagenFile(str(path))
    if audio is None:
        return None
    if ext == ".flac" and getattr(audio, "pictures", None):
        return audio.pictures[0].data
    if ext in {".opus", ".ogg", ".oga"}:
        b64 = (audio.tags or {}).get("metadata_block_picture")
        if b64:
            return Picture(base64.b64decode(b64[0])).data
    if ext in MP4_EXTS:
        covr = (audio.tags or {}).get("covr")
        if covr:
            return bytes(covr[0])
    if ext in MP3_EXTS and audio.tags:
        apics = audio.tags.getall("APIC")
        if apics:
            return apics[0].data
    return None


# --- Vorbis (FLAC/Opus/OGG) -------------------------------------------------

def _write_vorbis(path: Path, t: TrackTags) -> None:
    ext = path.suffix.lower()
    if ext == ".flac":
        audio = FLAC(str(path))
    elif ext == ".opus":
        audio = OggOpus(str(path))
    else:
        audio = OggVorbis(str(path))

    def setk(key: str, value) -> None:
        if value is not None and value != "":
            audio[key] = [str(value)]

    setk("title", t.title)
    setk("artist", t.artist)
    setk("album", t.album)
    setk("albumartist", t.album_artist)
    setk("date", t.date)
    setk("genre", t.genre)
    setk("lyrics", t.lyrics)
    if t.track_number is not None:
        setk("tracknumber", t.track_number)
    if t.total_tracks is not None:
        setk("tracktotal", t.total_tracks)
    setk("musicbrainz_trackid", t.musicbrainz_recordingid)
    setk("musicbrainz_albumid", t.musicbrainz_albumid)
    audio.save()


# --- MP4 (M4A/ALAC) ---------------------------------------------------------

def _write_mp4(path: Path, t: TrackTags) -> None:
    audio = MP4(str(path))

    def setk(atom: str, value) -> None:
        if value is not None and value != "":
            audio[atom] = [str(value)]

    setk("\xa9nam", t.title)
    setk("\xa9ART", t.artist)
    setk("\xa9alb", t.album)
    setk("aART", t.album_artist)
    setk("\xa9day", t.date)
    setk("\xa9gen", t.genre)
    setk("\xa9lyr", t.lyrics)
    if t.track_number is not None:
        total = t.total_tracks or 0
        audio["trkn"] = [(int(t.track_number), int(total))]
    audio.save()


# --- ID3 (MP3) --------------------------------------------------------------

def _load_id3(path: Path) -> ID3:
    try:
        return ID3(str(path))
    except ID3NoHeaderError:
        MP3(str(path)).add_tags()
        return ID3(str(path))


def _write_id3(path: Path, t: TrackTags) -> None:
    audio = _load_id3(path)
    if t.title:
        audio.setall("TIT2", [TIT2(encoding=3, text=[t.title])])
    if t.artist:
        audio.setall("TPE1", [TPE1(encoding=3, text=[t.artist])])
    if t.album:
        audio.setall("TALB", [TALB(encoding=3, text=[t.album])])
    if t.album_artist:
        audio.setall("TPE2", [TPE2(encoding=3, text=[t.album_artist])])
    if t.date:
        audio.setall("TDRC", [TDRC(encoding=3, text=[t.date])])
    if t.genre:
        audio.setall("TCON", [TCON(encoding=3, text=[t.genre])])
    if t.track_number is not None:
        trck = f"{t.track_number}/{t.total_tracks}" if t.total_tracks else str(t.track_number)
        audio.setall("TRCK", [TRCK(encoding=3, text=[trck])])
    if t.lyrics:
        audio.setall("USLT", [USLT(encoding=3, lang="spa", desc="", text=t.lyrics)])
    audio.save()


def _flac_picture(jpeg: bytes) -> Picture:
    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = "image/jpeg"
    pic.desc = "Cover"
    pic.data = jpeg
    return pic
