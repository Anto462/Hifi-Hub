"""Bus de eventos Python -> JS.

Solamente este punto del código toca `window.evaluate_js`. El resto del núcleo emite eventos aquí y este bus los serializa y empuja a la UI. Centralizarlo evita condiciones de carrera entre los distintos hilos que quieren hablar con el WebView.
"""

from __future__ import annotations

import json
import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._window: Any = None
        self._lock = threading.Lock()

    def bind(self, window: Any) -> None:
        """Asocia la ventana pywebview una vez creada."""
        self._window = window

    def emit(self, channel: str, payload: dict[str, Any] | None = None) -> None:
        """Invoca window.hub._receive(channel, payload).

        Silencioso si aún no hay ventana (tests) o si el WebView se está cerrando.
        """
        window = self._window
        if window is None:
            return
        data = json.dumps(payload or {}, ensure_ascii=False)
        code = f"window.hub && window.hub._receive({json.dumps(channel)}, {data})"
        with self._lock:
            try:
                window.evaluate_js(code)
            except Exception:
                pass
