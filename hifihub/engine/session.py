"""
Uso de Sesion de Youtube

1. IMPORTACIÓN ÚNICA: el usuario conecta una vez (desde su navegador o un
   cookies.txt). Después, la app no vuelve a tocar el navegador nunca.
2. MINIMIZACIÓN: del jar completo se conservan SOLO los dominios de
   youtube.com/google.com necesarios para autenticar. Banca, correo y todo lo
   demás se descarta en memoria y jamás se escribe.
3. CIFRADO EN REPOSO: la sesión se guarda cifrada con Windows DPAPI
   (CryptProtectData, ligada al usuario de Windows). Nada en claro en disco.
4. USO: en cada descarga se descifra a un archivo temporal en CONFIG_DIR (perfil
   del propio usuario) que se borra inmediatamente al terminar.
5. CONTROL: estado visible y "Cerrar sesión" borra el almacén.

Prioridad de fuentes de autenticación en el motor:
  sesión propia > cookies_file manual > cookiesfrombrowser
  
  Esto ya no se usa en la UI. El motivo fue que agregarlo no daba un valor real y que youtube se ha velto muy restrictivo con esto. Fuera de ello al enfocarse en el reproductor y todo lo que lo rodeaba esto era mas un extra.
  
  Dejo el code por si alguien le sirve.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hifihub import paths

# Solo estos sufijos de dominio sobreviven a la importación.
ALLOWED_DOMAIN_SUFFIXES = ("youtube.com", "google.com")

_DESCRIPTION = "HiFi Hub - sesion de YouTube"


class SessionError(RuntimeError):
    pass


def _session_file() -> Path:
    return paths.CONFIG_DIR / "session.dat"


# --- DPAPI (Windows, sin dependencias) ---------------------------------------

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_to_bytes(blob: _DATA_BLOB) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _to_blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_encrypt(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SessionError("El cifrado de sesión requiere Windows (DPAPI).")
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_to_blob(data)), _DESCRIPTION, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise SessionError("DPAPI no pudo cifrar la sesión.")
    return _blob_to_bytes(blob_out)


def dpapi_decrypt(blob: bytes) -> bytes:
    if sys.platform != "win32":
        raise SessionError("El descifrado de sesión requiere Windows (DPAPI).")
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_to_blob(blob)), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise SessionError("DPAPI no pudo descifrar la sesión (¿otro usuario de Windows?).")
    return _blob_to_bytes(blob_out)


# --- Filtrado y serialización -------------------------------------------------

def _domain_allowed(domain: str) -> bool:
    d = domain.lstrip(".").lower()
    return any(d == suf or d.endswith("." + suf) for suf in ALLOWED_DOMAIN_SUFFIXES)


def filter_jar(jar) -> Any:
    """Nuevo cookiejar SOLO con dominios de YouTube/Google."""
    from yt_dlp.cookies import YoutubeDLCookieJar

    filtered = YoutubeDLCookieJar()
    for cookie in jar:
        if _domain_allowed(cookie.domain or ""):
            filtered.set_cookie(cookie)
    return filtered


def _jar_to_netscape(jar) -> str:
    fd, name = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        jar.save(name, ignore_discard=True, ignore_expires=True)
        return Path(name).read_text(encoding="utf-8")
    finally:
        Path(name).unlink(missing_ok=True)


def _load_netscape_file(path: Path | str):
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


# --- Almacén ------------------------------------------------------------------

def _store(browser_or_source: str, netscape_text: str, n_cookies: int) -> dict[str, Any]:
    meta = {
        "source": browser_or_source,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cookies": n_cookies,
    }
    payload = json.dumps({"meta": meta, "netscape": netscape_text}).encode("utf-8")
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _session_file().write_bytes(dpapi_encrypt(payload))
    return meta


def _load() -> dict[str, Any] | None:
    f = _session_file()
    if not f.exists():
        return None
    try:
        payload = json.loads(dpapi_decrypt(f.read_bytes()).decode("utf-8"))
        if "netscape" not in payload:
            return None
        return payload
    except (SessionError, ValueError, OSError):
        return None


# --- API pública --------------------------------------------------------------

def connect_from_browser(browser: str) -> dict[str, Any]:
    """Importación única desde el navegador. Después no se vuelve a tocar."""
    from yt_dlp.cookies import extract_cookies_from_browser

    try:
        jar = extract_cookies_from_browser(browser)
    except Exception as e:  # noqa: BLE001 — yt-dlp lanza varios tipos
        from hifihub.engine.downloader import humanize_error

        raise SessionError(humanize_error(str(e))) from e

    filtered = filter_jar(jar)
    n = sum(1 for _ in filtered)
    if n == 0:
        raise SessionError(
            "No se encontró sesión de YouTube en ese navegador. "
            "Inicia sesión en youtube.com y reintenta."
        )
    return _store(browser, _jar_to_netscape(filtered), n)


def connect_from_file(cookies_txt: Path | str) -> dict[str, Any]:
    """Importa un cookies.txt exportado, filtra y lo guarda cifrado.

    Ventaja sobre usarlo en claro: tras importar, el usuario puede borrar el
    archivo original — la sesión queda solo en el almacén cifrado."""
    src = Path(cookies_txt)
    if not src.exists():
        raise SessionError(f"No existe: {src}")
    try:
        jar = _load_netscape_file(src)
    except Exception as e:  # noqa: BLE001
        raise SessionError(f"El archivo no es un cookies.txt válido: {e}") from e

    filtered = filter_jar(jar)
    n = sum(1 for _ in filtered)
    if n == 0:
        raise SessionError("El archivo no contiene cookies de YouTube/Google.")
    return _store(f"cookies.txt ({src.name})", _jar_to_netscape(filtered), n)


def status() -> dict[str, Any]:
    payload = _load()
    if payload is None:
        return {"connected": False}
    return {"connected": True, **payload.get("meta", {})}


def disconnect() -> None:
    _session_file().unlink(missing_ok=True)


@contextmanager
def session_cookiefile():
    """Descifra la sesión a un archivo temporal SOLO durante la operación.

    Yields la ruta (str) o None si no hay sesión. El temporal vive en
    CONFIG_DIR (perfil del usuario) y se borra al salir."""
    payload = _load()
    if payload is None:
        yield None
        return
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".sess-", suffix=".txt", dir=str(paths.CONFIG_DIR))
    try:
        os.write(fd, payload["netscape"].encode("utf-8"))
        os.close(fd)
        yield name
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(name).unlink(missing_ok=True)


def apply_session_to_opts(opts: dict[str, Any], cookiefile: str) -> None:
    """La sesión propia tiene prioridad sobre cualquier otra fuente de cookies."""
    opts["cookiefile"] = cookiefile
    opts.pop("cookiesfrombrowser", None)
