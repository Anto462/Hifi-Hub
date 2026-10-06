"""Análisis musical: BPM (tempo) y tonalidad (key).

Escribe los tags BPM e INITIALKEY que reproductores y software de DJ (Rekordbox, Serato, Traktor) leen. El análisis es costoso (decodifica y procesa la pista entera), por eso es opt-in, no parte de cada descarga por defecto. Se debe activar manualmente ya que retrasa la descarga.

Krumhansl-Schmuckler: se promedia el croma y se correlaciona con los perfiles tonales mayor/menor de las 12 tonalidades; gana la de
mayor correlación.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Perfiles tonales de Krumhansl-Schmuckler (mayor y menor).
_MAJOR_PROFILE = np.array( # Perfil mayor
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array( # Perfil menor
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


@dataclass(frozen=True)
class MusicAnalysis:
    bpm: int
    key: str          # ej. "A minor", "F# major"
    key_camelot: str  # notación Camelot (rueda de mezcla de DJ), ej. "8A"


# Rueda Camelot: (nota, modo) -> código. Estándar de mezcla.
_CAMELOT = {
    ("C", "major"): "8B", ("G", "major"): "9B", ("D", "major"): "10B",
    ("A", "major"): "11B", ("E", "major"): "12B", ("B", "major"): "1B",
    ("F#", "major"): "2B", ("C#", "major"): "3B", ("G#", "major"): "4B",
    ("D#", "major"): "5B", ("A#", "major"): "6B", ("F", "major"): "7B",
    ("A", "minor"): "8A", ("E", "minor"): "9A", ("B", "minor"): "10A",
    ("F#", "minor"): "11A", ("C#", "minor"): "12A", ("G#", "minor"): "1A",
    ("D#", "minor"): "2A", ("A#", "minor"): "3A", ("F", "minor"): "4A",
    ("C", "minor"): "5A", ("G", "minor"): "6A", ("D", "minor"): "7A",
}


def analyze(path: Path | str) -> MusicAnalysis:
    import librosa

    # Cargar mono a 22 kHz: suficiente para tempo/tonalidad y mucho más rápido.
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    if y.size == 0:
        raise ValueError("Audio vacío")

    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = int(round(float(np.atleast_1d(tempo)[0])))

    note, mode = _estimate_key(y, sr)
    return MusicAnalysis(
        bpm=bpm,
        key=f"{note} {mode}",
        key_camelot=_CAMELOT.get((note, mode), ""),
    )


def _estimate_key(y: np.ndarray, sr: int) -> tuple[str, str]:
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)  # energía media por clase de nota

    best_corr = -np.inf
    best = ("C", "major")
    for i in range(12):
        for mode, ref in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
            corr = np.corrcoef(profile, np.roll(ref, i))[0, 1]
            if corr > best_corr:
                best_corr = corr
                best = (_NOTES[i], mode)
    return best


def analyze_and_tag(path: Path | str) -> MusicAnalysis:
    """Analiza y escribe los tags BPM/INITIALKEY sin tocar el resto."""
    result = analyze(path)
    _write_extra(path, result)
    return result


def _write_extra(path: Path | str, result: MusicAnalysis) -> None:
    """Escribe BPM/INITIALKEY con mutagen según contenedor (campos que
    TrackTags no cubre)."""
    from mutagen import File as MutagenFile
    from mutagen.id3 import TBPM, TKEY

    from hifihub.metadata.tagger import MP3_EXTS, MP4_EXTS

    ext = Path(path).suffix.lower()
    if ext in MP3_EXTS:
        from mutagen.mp3 import MP3
        try:
            audio = MP3(str(path))
            if audio.tags is None:
                audio.add_tags()
        except Exception:
            return
        audio.tags.setall("TBPM", [TBPM(encoding=3, text=[str(result.bpm)])])
        audio.tags.setall("TKEY", [TKEY(encoding=3, text=[result.key_camelot or result.key])])
        audio.save()
    elif ext in MP4_EXTS:
        audio = MutagenFile(str(path))
        audio["----:com.apple.iTunes:BPM"] = [str(result.bpm).encode()]
        audio["----:com.apple.iTunes:initialkey"] = [result.key.encode()]
        audio.save()
    else:  # Vorbis (FLAC/Opus/OGG)
        audio = MutagenFile(str(path))
        audio["bpm"] = [str(result.bpm)]
        audio["initialkey"] = [result.key]
        audio.save()
