"""Actualizador de yt-dlp

Los extractores de YouTube en general se rompen cada pocas semanas por el como youtube se actualiza. Si no se permite actualizarlo desde el exe tendría las descargas rotas. con el tiempo.

- `hifihub/__init__` inserta `CONFIG_DIR/overrides/` al frente de `sys.path`.
- Actualizar = descargar el wheel oficial de yt-dlp desde PyPI, VERIFICAR su
  SHA-256 contra los metadatos (HTTPS), y extraer el paquete `yt_dlp/` en esa
  carpeta (a tmp + swap atómico). Al reiniciar, la app usa la versión nueva
  aunque el bundle traiga una vieja.
- En modo desarrollo (venv, no congelado) se usa `pip install -U yt-dlp`.

La UI avisara cuado hay una update
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from hifihub import paths

PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
# Canal nightly: los arreglos de YouTube (errores 403 / «formato no disponible»)
# aterrizan aquí antes que en la release estable de PyPI. Publica un sdist
# `yt-dlp.tar.gz` con su SHA-256 en `SHA2-256SUMS`.
NIGHTLY_API = "https://api.github.com/repos/yt-dlp/yt-dlp-nightly-builds/releases/latest"
_TIMEOUT = 20

STABLE, NIGHTLY = "stable", "nightly"


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    wheel_url: str          # URL de descarga (wheel para estable, sdist para nightly)
    sha256: str
    kind: str = "wheel"     # "wheel" (PyPI) | "sdist" (nightly tar.gz)


def overrides_dir() -> Path:
    return paths.CONFIG_DIR / "overrides"


def ensure_overrides_path() -> None:
    """Inserta la carpeta de overrides al frente de sys.path si existe.

    Debe ejecutarse ANTES de importar yt_dlp (lo llama hifihub/__init__)."""
    d = str(overrides_dir())
    if Path(d).is_dir() and d not in sys.path:
        sys.path.insert(0, d)


def current_version() -> str:
    import yt_dlp

    return yt_dlp.version.__version__


def active_source() -> str:
    """De dónde viene el yt_dlp en uso: 'override', 'bundle' (exe) o 'entorno'."""
    import yt_dlp

    module_path = Path(yt_dlp.__file__).resolve()
    if str(overrides_dir().resolve()) in str(module_path):
        return "override"
    return "bundle" if getattr(sys, "frozen", False) else "entorno"


def fetch_latest(channel: str = STABLE) -> ReleaseInfo:
    """Última versión del canal indicado (estable = PyPI, nightly = GitHub)."""
    return fetch_latest_nightly() if channel == NIGHTLY else fetch_latest_stable()


def fetch_latest_stable() -> ReleaseInfo:
    """Última versión publicada en PyPI + wheel universal + hash oficial."""
    try:
        resp = requests.get(PYPI_JSON, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise UpdateError(f"No se pudo consultar PyPI: {e}") from e

    version = data.get("info", {}).get("version")
    if not version:
        raise UpdateError("Respuesta de PyPI sin versión")
    for entry in data.get("urls", []):
        if entry.get("packagetype") == "bdist_wheel" and entry.get("filename", "").endswith("py3-none-any.whl"):
            sha = (entry.get("digests") or {}).get("sha256")
            if not sha:
                raise UpdateError("PyPI no publica el hash del wheel")
            return ReleaseInfo(version=version, wheel_url=entry["url"], sha256=sha, kind="wheel")
    raise UpdateError("PyPI no ofrece un wheel universal de yt-dlp")


def fetch_latest_nightly() -> ReleaseInfo:
    """Última nightly desde GitHub: sdist `yt-dlp.tar.gz` + hash de SHA2-256SUMS."""
    try:
        resp = requests.get(NIGHTLY_API, timeout=_TIMEOUT,
                            headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise UpdateError(f"No se pudo consultar las nightly de GitHub: {e}") from e

    version = data.get("tag_name")
    if not version:
        raise UpdateError("Release nightly sin tag")
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets", [])}
    tar_url = assets.get("yt-dlp.tar.gz")
    sums_url = assets.get("SHA2-256SUMS")
    if not tar_url or not sums_url:
        raise UpdateError("La nightly no ofrece yt-dlp.tar.gz + SHA2-256SUMS")
    sha = _sha_for(sums_url, "yt-dlp.tar.gz")
    return ReleaseInfo(version=version, wheel_url=tar_url, sha256=sha, kind="sdist")


def _sha_for(sums_url: str, filename: str) -> str:
    """Extrae el SHA-256 de `filename` del fichero SHA2-256SUMS (líneas 'hash  nombre')."""
    try:
        text = requests.get(sums_url, timeout=_TIMEOUT).text
    except requests.RequestException as e:
        raise UpdateError(f"No se pudo leer SHA2-256SUMS: {e}") from e
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == filename:
            return parts[0]
    raise UpdateError(f"SHA2-256SUMS no contiene {filename}")


def _version_tuple(v: str) -> tuple:
    parts = []
    for chunk in v.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def differs(latest: str, current: str) -> bool:
    """Normaliza con la misma tupla que ignora ceros."""
    return _version_tuple(latest) != _version_tuple(current)


def check(channel: str = STABLE) -> dict:
    """Estado de actualización. fallo de red devuelve ok=False."""
    from hifihub import __version__ as app_version

    try:
        latest = fetch_latest(channel)
    except UpdateError as e:
        return {"ok": False, "error": str(e), "app_version": app_version,
                "current": current_version(), "source": active_source(),
                "channel": channel}
    cur = current_version()
    return {
        "ok": True,
        "app_version": app_version,
        "current": cur,
        "latest": latest.version,
        "update_available": differs(latest.version, cur),
        "source": active_source(),
        "channel": channel,
    }


# --- Aplicar la actualización -------------------------------------------------

def apply_update(channel: str = STABLE) -> dict:
    """Instala la última versión del canal. Devuelve {ok, version, restart_required}."""
    latest = fetch_latest(channel)
    if not differs(latest.version, current_version()):
        return {"ok": True, "version": current_version(), "restart_required": False,
                "message": "Ya está actualizado.", "channel": channel}

    # El sdist de nightly y el wheel se instalan igual en exe y en desarrollo (los
    # overrides se anteponen a sys.path en ambos). `pip` solo sirve para la estable
    # en desarrollo.
    if latest.kind == "sdist":
        _install_sdist_to_overrides(_download_verified(latest))
    elif getattr(sys, "frozen", False):
        _install_wheel_to_overrides(_download_verified(latest))
    else:
        _pip_upgrade()
    return {"ok": True, "version": latest.version, "restart_required": True,
            "channel": channel,
            "message": f"Actualizado a {latest.version} ({channel}). Reinicia la aplicación para aplicar."}


def _download_verified(release: ReleaseInfo) -> bytes:
    try:
        resp = requests.get(release.wheel_url, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UpdateError(f"Descarga fallida: {e}") from e
    digest = hashlib.sha256(resp.content).hexdigest()
    if digest != release.sha256:
        raise UpdateError(
            "La verificación de integridad falló (SHA-256 no coincide). "
            "No se instala nada."
        )
    return resp.content


def _install_wheel_to_overrides(wheel_bytes: bytes) -> None:
    """Extrae el paquete yt_dlp del wheel en overrides/ (tmp + swap atómico)."""
    staging = Path(tempfile.mkdtemp(prefix="hifihub-upd-"))
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as z:
            members = [n for n in z.namelist() if n.startswith("yt_dlp/")]
            if not members:
                raise UpdateError("El wheel no contiene el paquete yt_dlp")
            z.extractall(staging, members)
        _swap_pkg_into_overrides(staging / "yt_dlp")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _install_sdist_to_overrides(tar_bytes: bytes) -> None:
    """Extrae el paquete yt_dlp del sdist nightly (yt-dlp/yt_dlp/…) en overrides/."""
    staging = Path(tempfile.mkdtemp(prefix="hifihub-upd-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as t:
            # El sdist trae 'yt-dlp/yt_dlp/…'; se reescriben los nombres para que
            # 'yt_dlp/' quede en la raíz del staging, y se descartan rutas raras
            # (path traversal) por seguridad.
            members = []
            for m in t.getmembers():
                parts = m.name.split("/", 1)
                if len(parts) == 2 and parts[1].startswith("yt_dlp/"):
                    if ".." in m.name or m.name.startswith("/"):
                        continue
                    m.name = parts[1]
                    members.append(m)
            if not members:
                raise UpdateError("El sdist no contiene el paquete yt_dlp")
            _tar_extract_safe(t, members, staging)
        _swap_pkg_into_overrides(staging / "yt_dlp")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _tar_extract_safe(t: tarfile.TarFile, members, dest: Path) -> None:
    if hasattr(tarfile, "data_filter"):        
        t.extractall(dest, members, filter="data")
    else:
        t.extractall(dest, members)


def _swap_pkg_into_overrides(staged_pkg: Path) -> None:
    """Reemplaza overrides/yt_dlp por el paquete recién extraído (swap atómico)."""
    overrides_dir().mkdir(parents=True, exist_ok=True)
    target = overrides_dir() / "yt_dlp"
    backup = target.with_name("yt_dlp.old")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    if target.exists():
        target.rename(backup)
    try:
        staged_pkg.rename(target)
    except OSError:
        shutil.copytree(staged_pkg, target)     # error en rename entre volúmenes
    shutil.rmtree(backup, ignore_errors=True)


def _pip_upgrade() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise UpdateError(f"pip falló: {(result.stderr or result.stdout).strip()[:300]}")
