"""Resolución de rutas de la aplicación y de los binarios embebidos."""

from __future__ import annotations

import os
from pathlib import Path

# Raíz del repositorio (hifihub/ vive dentro de ella)
REPO_ROOT = Path(__file__).resolve().parent.parent

# Carpeta con binarios embebidos: ffmpeg, ffprobe, libmpv, rsgain, fpcalc...
TOOLS_DIR = REPO_ROOT / "herramientas"

# Configuración y datos del usuario
CONFIG_DIR = Path(os.environ.get("HIFIHUB_HOME", str(Path.home() / ".hifihub")))
CONFIG_FILE = CONFIG_DIR / "config.toml"
DOWNLOAD_ARCHIVE = CONFIG_DIR / "download_archive.txt"


def tools_dir() -> Path:
    """Carpeta de binarios. Se pasa como ffmpeg_location a yt-dlp
    (un directorio permite que encuentre ffmpeg Y ffprobe)."""
    return TOOLS_DIR


def ffmpeg_exe() -> Path:
    return TOOLS_DIR / "ffmpeg.exe"


def ffprobe_exe() -> Path:
    return TOOLS_DIR / "ffprobe.exe"


def libmpv_dll() -> Path:
    return TOOLS_DIR / "libmpv-2.dll"


def rsgain_exe() -> Path:
    return TOOLS_DIR / "rsgain.exe"


def fpcalc_exe() -> Path:
    """Chromaprint: genera la huella acústica para AcoustID."""
    return TOOLS_DIR / "fpcalc.exe"


def default_music_dir() -> Path:
    return Path.home() / "Music" / "HiFi Hub"
