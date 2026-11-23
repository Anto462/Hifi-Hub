"""Arranque de la aplicación de escritorio pywebview."""

from __future__ import annotations

import sys
from pathlib import Path

from hifihub.ui.api import Api
from hifihub.ui.events import EventBus

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX = WEB_DIR / "index.html"


def _icon_path() -> str | None:
    """Localiza el icono de la app en el exe congelado o en el repo."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "assets" / "mi_icono.ico")
    candidates.append(Path(__file__).resolve().parents[2] / "assets" / "mi_icono.ico")
    for c in candidates:
        if c and c.exists():
            return str(c)
    return None


def widen_http_backlog(size: int = 128) -> None:
    """Amplía la cola de conexiones del servidor HTTP local de pywebview.

    pywebview sirve la interfaz desde un ``wsgiref`` (ThreadingMixIn + WSGIServer)
    que hereda ``request_queue_size = 5``. La página pide ~10 archivos a la vez
    (6 .css + 4 .js) y, en cuanto hay más de 5 conexiones pendientes, Windows
    RECHAZA las sobrantes: esos .js/.css no llegan a cargar (estado 0, 0 bytes),
    ``init()`` no termina y la ventana se queda congelada a medio pintar. Medido:
    falla en 5 de 8 arranques sin este ajuste.
    """
    from wsgiref.simple_server import WSGIServer

    WSGIServer.request_queue_size = max(WSGIServer.request_queue_size, size)


def run(debug: bool = False) -> None:
    import webview

    widen_http_backlog()   # antes de webview.start(): el servidor se crea ahí

    bus = EventBus()
    api = Api(bus)

    window = webview.create_window(
        title="HiFi Hub",
        url=str(INDEX),
        js_api=api,
        width=900,
        height=680,
        min_size=(720, 520),
    )
    # El bus y la API necesitan la ventana para evaluate_js y los diálogos.
    bus.bind(window)
    api.bind_window(window)
    # Al cerrar: guardar dónde se quedó la reproducción (se retoma al volver).
    window.events.closing += api._shutdown

    icon = _icon_path()
    # En Windows el icono de ventana se hereda del .exe (lo fija el spec de  PyInstaller); en dev/otros GUIs, pywebview lo toma del parámetro icon.
    start_kwargs = {"debug": debug}
    if icon:
        start_kwargs["icon"] = icon
    webview.start(**start_kwargs)
