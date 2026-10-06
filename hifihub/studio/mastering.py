"""Mastering.

Iguala la tonalidad y el loudness de una pista a los de una REFERENCIA que suene
como el usuario quiere. Genera una copia nueva; el original no cambia. Requiere el
paquete `matchering` (extra 'studio'); si no está, degrada con un error claro.
"""

from __future__ import annotations

from pathlib import Path

from hifihub import paths
from hifihub.studio.chain import StudioError, _run_ffmpeg

try:
    import matchering as mg  # importa y cambia el estado  a true
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


def match_reference(
    target: Path | str,
    reference: Path | str,
    dst: Path | str,
) -> Path:
    """Masteriza `target` para que se parezca a `reference`. Escribe FLAC en `dst`."""
    if not AVAILABLE:
        raise StudioError("matchering no está instalado (extra 'studio').")
    import matchering as mg

    target, reference, dst = Path(target), Path(reference), Path(dst)
    for f in (target, reference):
        if not f.exists():
            raise StudioError(f"No existe: {f}")

    # matchering lee WAV con fiabilidad: decodificar ambos con ffmpeg primero.
    from hifihub.studio.chain import _mkstemp_closed

    t_wav = _mkstemp_closed(".wav")
    r_wav = _mkstemp_closed(".wav")
    out_wav = _mkstemp_closed(".wav")
    try:
        _run_ffmpeg(["-i", str(target), "-c:a", "pcm_f32le", str(t_wav)])
        _run_ffmpeg(["-i", str(reference), "-c:a", "pcm_f32le", str(r_wav)])
        mg.process(
            target=str(t_wav),
            reference=str(r_wav),
            results=[mg.pcm24(str(out_wav))],
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(["-i", str(out_wav), "-c:a", "flac", str(dst)])
        return dst
    finally:
        for f in (t_wav, r_wav, out_wav):
            f.unlink(missing_ok=True)
