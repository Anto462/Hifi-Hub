"""Convolución por respuesta al impulso (IR) en tiempo real.

E vez de aproximar con biquads, aplica la respuesta medida de una sala o un auricular. Busca la corrección de sala medida con REW (que exporta el impulso en .wav).

Se apoya en el filtro `afir` de ffmpeg, cargando el IR dentro del propio graph con `amovie`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from hifihub import paths

IR_DIR = paths.CONFIG_DIR / "impulses"
IR_EXTS = frozenset({".wav", ".flac", ".aiff", ".aif"})


class ConvolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Impulse:
    name: str
    path: Path
    channels: int | None = None
    sample_rate: int | None = None
    duration: float | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "duration": self.duration,
        }


def _escape(path: Path) -> str:
    """Escapa una ruta de Windows para incrustarla en un graph de lavfi.

    En la sintaxis de filtros, `\\` y `:` son especiales; además el valor va
    entre comillas simples dentro de `amovie='...'`.
    """
    text = str(path).replace("\\", "/")      # lavfi acepta / en Windows
    return text.replace("'", r"\'").replace(":", r"\:")


def af_fragment_for(path: str | Path | None, mix: float = 1.0) -> tuple[str, str]:
    """Devuelve (prefijo, filtro) para insertar la convolución en la cadena.

    `afir` necesita DOS entradas (señal + IR), así que no es un filtro encadenable
    con comas: hay que etiquetar la señal y traer el IR con `amovie`. El llamador
    monta:  <cadena previa>[main];<prefijo>;[main][ir]<filtro>,<cadena posterior>
    """
    if not path:
        return "", ""
    p = Path(path)
    if not p.exists():
        raise ConvolutionError(f"No existe el impulso: {p.name}")
    wet = max(0.0, min(1.0, float(mix)))
    prefix = f"amovie='{_escape(p)}'[ir]"
    # gtype=peak normaliza la ganancia del IR para que la convolución no recorte.
    filt = f"afir=dry=1:wet={wet:g}:gtype=peak"
    return prefix, filt


def list_impulses() -> list[dict]:
    """Impulsos disponibles en la carpeta del usuario."""
    if not IR_DIR.is_dir():
        return []
    out = []
    for p in sorted(IR_DIR.iterdir()):
        if p.suffix.lower() in IR_EXTS and p.is_file():
            out.append(probe_impulse(p).as_dict())
    return out


def probe_impulse(path: Path) -> Impulse:
    """Datos del IR (canales, frecuencia, duración) con ffprobe. Tolerante."""
    channels = sample_rate = None
    duration = None
    try:
        from hifihub.audio.inspect import probe

        info = probe(path)
        channels = getattr(info, "channels", None)
        sample_rate = getattr(info, "sample_rate", None)
        duration = getattr(info, "duration", None)
    except Exception:  # los metadatos son informativos
        pass
    return Impulse(name=path.stem, path=path, channels=channels,
                   sample_rate=sample_rate, duration=duration)


def import_impulse(src: str | Path) -> dict:
    """Copia un .wav de impulso a la carpeta del usuario y lo valida."""
    src = Path(src)
    if not src.exists():
        raise ConvolutionError(f"No existe el archivo: {src}")
    if src.suffix.lower() not in IR_EXTS:
        raise ConvolutionError("El impulso debe ser .wav, .flac o .aiff.")
    IR_DIR.mkdir(parents=True, exist_ok=True)
    dst = IR_DIR / src.name
    if dst.resolve() != src.resolve():
        shutil.copy2(src, dst)
    imp = probe_impulse(dst)
    if imp.duration is not None and imp.duration > 30:
        # Un IR de más de 30 s no es una medición: probablemente sea música o un archivo erroneo.
        dst.unlink(missing_ok=True)
        raise ConvolutionError("Ese archivo dura demasiado para ser un impulso.")
    return imp.as_dict()


def delete_impulse(name: str) -> None:
    for p in IR_DIR.glob(f"{name}.*"):
        if p.suffix.lower() in IR_EXTS:
            p.unlink(missing_ok=True)


def find_impulse(name: str | None) -> Path | None:
    if not name:
        return None
    for p in IR_DIR.glob(f"{name}.*"):
        if p.suffix.lower() in IR_EXTS:
            return p
    return None
