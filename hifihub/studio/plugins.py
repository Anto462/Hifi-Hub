"""Descubrimiento y consulta de plugins VST3 del usuario.

Permite al user insertar sus plugins de mastering (EQ, compresores, saturadores)
en la cadena offline. Aquí se localizan los .vst3 instalados y se listan sus
parámetros para exponerlos en la UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Rutas estándar de VST3 en Windows (y algún fallback multiplataforma).
_VST3_DIRS = [
    Path(os.environ.get("COMMONPROGRAMFILES", r"C:\Program Files\Common Files")) / "VST3",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Common Files" / "VST3",
    Path.home() / ".vst3",
]


@dataclass(frozen=True)
class PluginInfo:
    name: str
    path: str


def scan_vst3(extra_dirs: list[Path] | None = None) -> list[PluginInfo]:
    """Lista los .vst3 encontrados en las rutas estándar (+ extra)."""
    found: dict[str, PluginInfo] = {}
    dirs = list(_VST3_DIRS) + [Path(d) for d in (extra_dirs or [])]
    for d in dirs:
        if not d.exists():
            continue
        for vst in d.glob("**/*.vst3"):
            found[vst.stem] = PluginInfo(name=vst.stem, path=str(vst))
    return sorted(found.values(), key=lambda p: p.name.lower())


def plugin_parameters(path: str) -> dict[str, object]:
    """Parámetros expuestos por un plugin (nombre -> valor actual)."""
    from hifihub.studio.chain import AVAILABLE, StudioError

    if not AVAILABLE:
        raise StudioError("pedalboard no está instalado.")
    import pedalboard as pb

    plugin = pb.load_plugin(path)
    params: dict[str, object] = {}
    for name in getattr(plugin, "parameters", {}):
        try:
            params[name] = getattr(plugin, name)
        except Exception:  # noqa: BLE001
            pass
    return params
