"""Oculta las ventanas de consola de los subprocesos hijos en Windows.

La app se empaqueta SIN consola (console=False). En Windows, un proceso sin
consola que lanza un ejecutable de consola (ffmpeg, ffprobe, rsgain, fpcalc…)
provoca que Windows abra una ventana de consola nueva para el hijo. yt-dlp ya
oculta las suyas, pero nuestras llamadas y las de terceros (p. ej. `pyacoustid`,
que lanza `fpcalc` durante el enriquecido) no.

Esto busca no tocar cada `subprocess.run` por separado, sino se parchea una sola vez el
`subprocess.Popen` para añadir por defecto la bandera `CREATE_NO_WINDOW`. Cubre
nuestras llamadas, las de terceros y las futuras. Solo se activa en el EXE
congelado y en Windows: en desarrollo se conserva la consola para depurar.

Realmnte esto fue agregado por la queja de mis amigos lol.
"""

from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
NORMAL_PRIORITY_CLASS = 0x00000020

# "Modo Escucha": mientras suena música, los subprocesos pesados (ffmpeg de
# espectros, rsgain de lotes, análisis de BPM…) nacen con prioridad baja para que
# la reproducción tenga preferencia de CPU. No se aplaza ni se bloquea nada: solo
# se cede el turno, así que las tareas siguen avanzando.
_background_children = False


def set_background_children(enabled: bool) -> None:
    global _background_children
    _background_children = bool(enabled)


def background_children_enabled() -> bool:
    return _background_children


def _should_hide() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def with_hidden_flag(kwargs: dict) -> dict:
    """Devuelve una copia de kwargs con las banderas de creación que toquen.Función pura y aparte para poder testearla sin lanzar procesos reales.
    """
    flags = kwargs.get("creationflags") or 0
    flags |= CREATE_NO_WINDOW
    if _background_children:
        flags |= BELOW_NORMAL_PRIORITY_CLASS
    return {**kwargs, "creationflags": flags}


def set_process_priority(high: bool) -> bool:
    """Sube o restaura la prioridad del proceso de la app.
    Con la reproducción activa conviene que el hilo de audio no espere detrás de trabajos de fondo. Devuelve True si se pudo aplicar.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetCurrentProcess()
        cls = ABOVE_NORMAL_PRIORITY_CLASS if high else NORMAL_PRIORITY_CLASS
        return bool(ctypes.windll.kernel32.SetPriorityClass(handle, cls))
    except Exception:  # noqa: BLE001 — la prioridad es un extra, nunca crítica
        return False


def hide_child_consoles(force: bool = False) -> bool:
    """Parchea subprocess.Popen para ocultar la ventana de los hijos. Idempotente.
    Devuelve True si el parche quedó activo.
    """
    if not (force or _should_hide()):
        return False
    if getattr(subprocess.Popen, "_hifihub_hidden", False):
        return True

    _orig_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):
        try:
            kwargs = with_hidden_flag(kwargs)
        except Exception:  # noqa: BLE001 — nunca romper el lanzamiento por esto
            pass
        try:
            _orig_init(self, *args, **kwargs)
        except TypeError:
            # Error de creationflags pasado posicionalmente -> reintentar sin tocar.
            kwargs.pop("creationflags", None)
            _orig_init(self, *args, **kwargs)

    _init._hifihub_hidden = True
    subprocess.Popen.__init__ = _init  # type: ignore[method-assign]
    subprocess.Popen._hifihub_hidden = True  # type: ignore[attr-defined]
    return True
