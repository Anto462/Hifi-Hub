"""Wrapper de libmpv (python-mpv) para reproducción bit-perfect.

libmpv posee su propia lista de reproducción; se usa para conseguir gapless
real.
El resto del sistema controla la reproducción a través de esta clase, nunca tocando mpv
directamente.

Ajustes por defecto:
- solo audio (sin decodificar vídeo)
- gapless-audio: los álbumes/mixes suenan continuos
- replaygain: consume los tags que rsgain escribirá paea un volumen uniforme
  sin recodificar
- audio-exclusive opcional: WASAPI exclusivo, el DAC recibe el stream intacto
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from hifihub import paths

# libmpv-2.dll vive en herramientas/; hay que añadirlo a la ruta de búsqueda de
# DLLs ANTES de importar el módulo mpv.
_TOOLS = str(paths.tools_dir())
if hasattr(os, "add_dll_directory") and os.path.isdir(_TOOLS):
    os.add_dll_directory(_TOOLS)
os.environ["PATH"] = _TOOLS + os.pathsep + os.environ.get("PATH", "")

EventCallback = Callable[[str, Any], None]


class PlayerError(RuntimeError):
    pass


class MpvPlayer:
    def __init__(
        self,
        on_event: EventCallback | None = None,
        replaygain: str = "track",
        ao: str | None = None,
    ) -> None:
        try:
            import mpv
        except OSError as e:  # DLL no encontrada
            raise PlayerError(f"No se pudo cargar libmpv: {e}") from e

        self._on_event = on_event
        self._busy_at = 0.0   # último aviso de "dispositivo ocupado" (antes de crear mpv)
        kwargs: dict[str, Any] = dict(
            video=False,
            ytdl=False,
            gapless_audio="yes",
            audio_display="no",
            replaygain=replaygain,
            # Solo errores: es el único canal por el que libmpv delata un
            # dispositivo ocupado (ver _on_log). Nivel bajo = casi sin tráfico.
            log_handler=self._on_log,
            loglevel="error",
        )
        if ao is not None:  # 'null' en tests headless
            kwargs["ao"] = ao
        self._mpv = mpv.MPV(**kwargs)
        self._wire_observers()

    def _emit(self, name: str, value: Any) -> None:
        if self._on_event is not None:
            self._on_event(name, value)

    # Cada cuánto, como mucho, se avisa del dispositivo ocupado: mpv reintenta
    # abrirlo en bucle y escribe el mismo error varias veces por segundo.
    BUSY_EVERY = 5.0

    def _on_log(self, level: str, component: str, message: str) -> None:
        """Detecta el dispositivo de audio ocupado por otra app en exclusiva.

        Midiendo con este libmpv con el dispositivo tomado en exclusiva por otro
        proceso NO hay `end-file` ni salto de pista. mpv se queda reintentando
        (log: AUDCLNT_E_DEVICE_IN_USE) y el audio no avanza. El log es la única señal.
        """
        if component != "ao/wasapi" or "DEVICE_IN_USE" not in message:
            return
        now = time.monotonic()
        if now - self._busy_at < self.BUSY_EVERY:
            return
        self._busy_at = now
        self._emit("device-busy", None)

    def _wire_observers(self) -> None:
        m = self._mpv

        @m.property_observer("time-pos")
        def _pos(_name, value):  # ANN001
            self._emit("time-pos", value)

        @m.property_observer("duration")
        def _dur(_name, value):  # ANN001
            self._emit("duration", value)

        @m.property_observer("pause")
        def _pause(_name, value):  # ANN001
            self._emit("pause", value)

        @m.property_observer("playlist-pos")
        def _plpos(_name, value):  # ANN001
            if value is not None and value >= 0:
                self._emit("playlist-pos", value)

        @m.event_callback("end-file")
        def _end(event):  # ANN001
            reason = getattr(event, "data", None)
            self._emit("end-file", reason)

    # --- Lista de reproducción -----------------------------------------

    def load(self, files: list[str], start: int = 0) -> None:
        """Carga una lista (reemplazando la actual) y empieza en `start`."""
        if not files:
            return
        self._mpv.command("loadfile", files[0], "replace")
        for f in files[1:]:
            self._mpv.command("loadfile", f, "append")
        if start > 0:
            self._mpv.playlist_pos = start
        self._mpv.pause = False

    def load_at(self, files: list[str], start: int = 0, position: float = 0.0) -> None:
        """Carga una lista y empieza en `start`, en el segundo `position`.

        Para retomar una sesión. A diferencia de `load()`, no arranca la primera
        pista para saltar después: se vacía, se añaden todas SIN reproducir y se
        salta a `start`. Medido: así mpv emite un único `playlist-pos` (el de
        `start`) en vez de 0 y luego `start`. El `start=` va solo en esa pista, así
        que la siguiente empieza desde el principio.
        """
        if not files:
            return
        self._mpv.command("stop")
        for i, f in enumerate(files):
            if i == start and position > 0:
                self._mpv.command("loadfile", f, "append", -1, f"start={position:.3f}")
            else:
                self._mpv.command("loadfile", f, "append")
        self._mpv.playlist_pos = start
        self._mpv.pause = False

    def play_index(self, index: int) -> None:
        self._mpv.playlist_pos = index
        self._mpv.pause = False

    def move(self, src: int, dst: int) -> None:
        """Mueve una entrada de la lista sin cortar lo que suena.

        `playlist-move` de mpv inserta ANTES de `dst`  previa al
        movimiento para dejarla en la posición final `dst`. En cuanto avanza hay
        que apuntar una más allá."""
        self._mpv.command("playlist-move", src, dst + 1 if dst > src else dst)

    def append_file(self, path: str) -> None:
        """Añade una pista al final de la lista de mpv sin cortar lo que suena."""
        self._mpv.command("loadfile", path, "append")

    def next(self) -> None:
        try:
            self._mpv.command("playlist-next", "weak")
        except Exception:  # no hay siguiente
            pass

    def prev(self) -> None:
        try:
            self._mpv.command("playlist-prev", "weak")
        except Exception:  # error 
            pass

    # --- Transporte -----------------------------------------------------

    def toggle_pause(self) -> bool:
        self._mpv.pause = not self._mpv.pause
        return self._mpv.pause

    def set_pause(self, value: bool) -> None:
        self._mpv.pause = value

    def stop(self) -> None:
        self._mpv.command("stop")

    def seek(self, seconds: float) -> None:
        self._mpv.command("seek", seconds, "absolute")

    def set_volume(self, volume: float) -> None:
        self._mpv.volume = max(0, min(100, volume))

    # --- Configuración audiófila ---------------------------------------

    def set_replaygain(self, mode: str) -> None:
        # 'no' | 'track' | 'album'
        self._mpv.replaygain = mode

    def set_replaygain_preamp(self, db: float) -> None:
        """Ganancia adicional sobre el nivel ReplayGain (dB).

        RG2 nivela a -18 LUFS: a masters modernos les aplica ganancia negativa. Un preamp de +3 a +6 dB compensa."""
        self._mpv["replaygain-preamp"] = float(db)

    def set_exclusive(self, enabled: bool) -> None:
        self._mpv["audio-exclusive"] = "yes" if enabled else "no"

    def set_device_keepalive(self, enabled: bool, wait_open: float = 0.0) -> None:
        """
        En exclusivo, cada apertura/cierre del dispositivo hace que muchos DAC
        conmuten su relé y que se pierdan los primeros milisegundos.
        al re-sincronizar el reloj. `audio-stream-silence` mantiene el flujo vivo
        enviando silencio, y `audio-wait-open` da margen al DAC tras abrirlo.
        """
        try:
            self._mpv["audio-stream-silence"] = "yes" if enabled else "no"
            self._mpv["audio-wait-open"] = float(wait_open) if enabled else 0.0
        except Exception:  # libmpv antiguo
            pass

    def set_audio_device(self, device: str) -> None:
        self._mpv["audio-device"] = device

    def list_audio_devices(self) -> list[dict[str, str]]:
        try:
            return [
                {"name": d.get("name", ""), "description": d.get("description", "")}
                for d in self._mpv.audio_device_list or []
            ]
        except Exception:  # BLE001
            return []

    def state(self) -> dict[str, Any]:
        m = self._mpv
        return {
            "playing": not m.pause if m.pause is not None else False,
            "position": m.time_pos,
            "duration": m.duration,
            "volume": m.volume,
            "playlist_pos": m.playlist_pos,
        }

    def terminate(self) -> None:
        try:
            self._mpv.terminate()
        except Exception:  # BLE001
            pass
