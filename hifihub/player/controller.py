"""Controller maestro que une la cola, el motor libmpv y la biblioteca.

Mantiene la cola espejada para la UI, aplica shuffle/repeat, marca reproducciones
y traduce los eventos crudos de mpv a eventos de alto nivel para el frontend.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Callable

from hifihub.library.db import Library
from hifihub.player.dsp import Equalizer
from hifihub.player.mpv_backend import MpvPlayer

EventCallback = Callable[[str, dict], None]

REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = "off", "all", "one"
# Motivos de fin de archivo de mpv (MpvEventEndFile) que cierran la cola: EOF (fin
# natural de la pista) y ERROR (no se pudo abrir: mpv se queda inactivo, sin audio).
END_EOF, END_ERROR = 0, 4
END_FINAL = (END_EOF, END_ERROR)


def _row_to_track(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "path": row["path"],
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "duration": row["duration"],
        "cover_path": row["cover_path"],
        # Ficha técnica (specs reales medidas al escanear).
        "codec": row["codec"],
        "sample_rate": row["sample_rate"],
        "bit_depth": row["bit_depth"],
        "bit_rate": row["bit_rate"],
        "lossless": bool(row["lossless"]),
    }


class PlaybackController:
    def __init__(
        self,
        library: Library,
        on_event: EventCallback | None = None,
        ao: str | None = None,
        enable_smtc: bool = False,
        scrobbler: Any = None,
    ) -> None:
        self.lib = library
        self._on_event = on_event
        self._queue: list[dict[str, Any]] = []
        self._index: int = -1
        self._shuffle = False
        self._repeat = REPEAT_OFF
        self._last_pos = -1
        self._volume = 100.0
        self._eq = Equalizer()
        self._headphone: str | None = None   # nombre del preset AutoEq activo
        self._crossfeed = 0.0                  # 0 = off, 0..1 intensidad
        self._replaygain_mode = "track"
        self._replaygain_preamp = 0.0
        self._exclusive = False
        # Ajustes de dispositivo.
        self._keepalive = True
        self._wait_open = 0.3
        self._limiter = False                  # red de seguridad de picos (opt-in)
        self._listening_mode = False           # prioriza el audio bajo carga
        self._solo_band: int | None = None     # banda aislada del EQ (afinar de oído)
        self._convolution: str | None = None   # ruta del IR activo
        # Pista retomada de la sesión anterior: ya se contó al escucharla, así que
        # su primer cambio de pista no vuelve a sumar una reproducción.
        self._resume_id: int | None = None
        self._resume_announced = False   # ¿ya llegó su primer playlist-pos?
        # Índice al que debe llegar mpv tras restore(): en un mpv recién creado llega
        # a veces antes un playlist-pos 0 inicial (medido: 4 de 15 arranques).
        self._await_pos: int | None = None
        # Índice que mpv anunciará como eco de un reordenamiento de la cola: ese
        # playlist-pos no es un cambio de pista y se ignora (ver move_in_queue).
        self._expect_pos: int | None = None
        # Scrobbling (Last.fm): seguimiento de cuánto sonó la pista actual.
        self._scrobbler = scrobbler
        self._played_track: dict[str, Any] | None = None
        self._played_started = 0.0
        self._played_secs = 0.0
        self._smtc = None
        # El player se crea AL FINAL: sus observadores llaman a _on_mpv_event,
        # que lee _smtc/_scrobbler/etc. — deben existir antes.
        self._player = MpvPlayer(on_event=self._on_mpv_event, ao=ao)
        # Teclas multimedia / overlay de Windows (degradación elegante).
        if enable_smtc:
            from hifihub.player.smtc import MediaControls
            self._smtc = MediaControls({
                "play": lambda: self._player.set_pause(False),
                "pause": lambda: self._player.set_pause(True),
                "next": self.next,
                "prev": self.prev,
            })

    # --- API pública ----------------------------------------------------

    def play_tracks(self, rows, start: int = 0) -> None:
        self._queue = [_row_to_track(r) for r in rows]
        if not self._queue:
            return
        if self._shuffle:
            self._apply_shuffle(keep=start)
            start = 0
        self._index = start
        self._player.load([t["path"] for t in self._queue], start)
        self._emit_track()

    def play_album(self, album: str | None) -> None:
        self.play_tracks(self.lib.album_tracks(album))

    def restore(self, rows, index: int = 0, position: float = 0.0, shuffle: bool = False,
                repeat: str = REPEAT_OFF, volume: float = 100.0, paused: bool = False) -> None:
        """Retoma una cola tal cual se dejó: mismo orden, pista, segundo y modos.

        El orden guardado YA es el barajado, así que el aleatorio solo se marca, no
        se vuelve a barajar. Emite `player:modes` para que la interfaz sincronice
        sus botones de aleatorio/repetir y el volumen. Con `paused` queda cargada
        en pausa.
        """
        self._queue = [_row_to_track(r) for r in rows]
        if not self._queue:
            return
        self._index = max(0, min(int(index), len(self._queue) - 1))
        self._shuffle = bool(shuffle)
        self._resume_id = self._queue[self._index].get("id")
        self._resume_announced = False
        self._await_pos = self._index if self._index > 0 else None
        self.set_volume(volume)
        self._player.load_at([t["path"] for t in self._queue], self._index,
                             max(0.0, float(position or 0)))
        if paused:
            self._player.set_pause(True)
        else:
            # Si mpv ya estaba sin pausa no hay evento `pause`: se avisa a mano para
            # que los botones no se queden en «play» con el audio sonando.
            self._emit("player:state", {"playing": True})
        self.set_repeat(repeat)
        # Sin _emit_track(): load_at provoca UN único playlist-pos (medido) y es
        # ese evento el que la anuncia; llamarlo aquí lo duplicaría.
        self._emit("player:modes", {"shuffle": self._shuffle, "repeat": self._repeat,
                                    "volume": self._volume})

    def stop(self) -> None:
        """Detiene y vacía la cola: mpv suelta los archivos que tenía abiertos.

        Lo usa la API antes de reescribir el archivo que suena (tags, portada).
        El estado se avisa aquí, en orden: el `end-file` de esta parada llega
        asíncrono, pero con motivo ABORTED, y la rama end-file lo ignora.
        """
        self._queue = []
        self._index = -1
        self._player.stop()
        self._emit("player:state", {"playing": False})

    def flush_scrobble(self) -> None:
        """Decide el scrobble de la pista en curso cuando se cierra la app."""
        self._maybe_scrobble()

    def add_to_queue(self, rows) -> int:
        """Añade pistas al final de la cola sin interrumpir. Si no hay nada
        sonando, arranca la reproducción. Devuelve cuántas se añadieron."""
        tracks = [_row_to_track(r) for r in rows]
        if not tracks:
            return 0
        if not self._queue:
            self.play_tracks(rows)
            return len(tracks)
        for t in tracks:
            self._player.append_file(t["path"])
        self._queue.extend(tracks)
        self._emit("player:queue", self.get_queue())
        self._emit_neighbours()   # encolar cambia la "siguiente"
        return len(tracks)

    def move_in_queue(self, src: int, dst: int) -> bool:
        """Reordena la cola, permitiendo arrastrar las pistas dentro de la cola sin interrumpir la escucha.

        `dst` es la posición FINAL de la pista. Medido: mover entradas en mpv
        cambia su playlist-pos aunque siga sonando la misma pista; ese eco se
        marca en `_expect_pos` y se ignora, para no contar otra reproducción ni
        reiniciar la escena del tocadiscos. Solo se marca si el índice cambia de
        verdad. si no, mpv no avisa y el marcador se tragaría un cambio real.
        """
        n = len(self._queue)
        if not (0 <= src < n and 0 <= dst < n) or src == dst:
            return False
        cur = self._index
        if src == cur:
            new = dst
        elif src < cur <= dst:
            new = cur - 1
        elif dst <= cur < src:
            new = cur + 1
        else:
            new = cur
        self._queue.insert(dst, self._queue.pop(src))
        self._index = new
        if new != cur:
            self._expect_pos = new
        self._player.move(src, dst)
        self._emit("player:queue", self.get_queue())
        self._emit_neighbours()
        return True

    def current_path(self) -> str | None:
        t = self.current_track()
        return t["path"] if t else None

    def play_file(self, path: str) -> None:
        """Reproduce un archivo suelto (para preview A/B del estudio)."""
        self._queue = [{"id": None, "path": path, "title": Path(path).stem,
                        "artist": None, "album": None, "duration": None,
                        "cover_path": None, "codec": None, "sample_rate": None,
                        "bit_depth": None, "bit_rate": None, "lossless": False}]
        self._index = 0
        self._player.load([path], 0)

    def set_scrobbler(self, scrobbler: Any) -> None:
        """Conecta/desconecta Last.fm en caliente (None = sin scrobbling).

        La pista en curso conserva su cronómetro: si se conecta a mitad de una
        canción, esa canción también cuenta al terminar."""
        self._scrobbler = scrobbler

    def toggle_pause(self) -> dict:
        paused = self._player.toggle_pause()
        return {"playing": not paused}

    def next(self) -> None:
        self._player.next()

    def prev(self) -> None:
        self._player.prev()

    def play_index(self, index: int) -> None:
        """Salta a una posición concreta de la cola (panel de Cola)."""
        if 0 <= index < len(self._queue):
            self._player.play_index(index)

    def seek(self, seconds: float) -> None:
        self._player.seek(seconds)

    def set_volume(self, volume: float) -> None:
        self._volume = volume
        self._player.set_volume(volume)

    def set_shuffle(self, enabled: bool) -> None:
        self._shuffle = enabled
        # Reordena la cola restante manteniendo la pista actual al frente.
        if enabled and self._queue and 0 <= self._index < len(self._queue):
            self._apply_shuffle(keep=self._index)
            self._index = 0
            self._player.load([t["path"] for t in self._queue], 0)

    def set_repeat(self, mode: str) -> None:
        self._repeat = mode if mode in (REPEAT_OFF, REPEAT_ALL, REPEAT_ONE) else REPEAT_OFF
        m = self._player._mpv
        m["loop-file"] = "inf" if self._repeat == REPEAT_ONE else "no"
        m["loop-playlist"] = "inf" if self._repeat == REPEAT_ALL else "no"
        # Repetir-todo hace que la cola dé la vuelta: cambian los vecinos. Se emite
        # un evento propio en vez de _emit_track(), que reiniciaría el cronómetro
        # de scrobble de la pista en curso.
        self._emit_neighbours()

    def set_replaygain(self, mode: str) -> None:
        # Acepta "off" (alias de "no" de mpv), "track" o "album".
        self._replaygain_mode = "no" if mode == "off" else mode
        self._player.set_replaygain(self._replaygain_mode)
        self._emit("player:signalpath", self.signal_path())

    def set_replaygain_preamp(self, db: float) -> None:
        self._replaygain_preamp = float(db)
        self._player.set_replaygain_preamp(db)

    def set_exclusive(self, enabled: bool) -> None:
        """Modo exclusivo (WASAPI). Arrastra consigo los ajustes de dispositivo.

        Las opciones de "mantener el DAC despierto" solo tienen sentido cuando la
        app toma el dispositivo en exclusiva: en compartido el mezclador de Windows
        ya lo mantiene abierto. Por eso viajan juntas con este interruptor.
        """
        self._exclusive = bool(enabled)
        self._player.set_exclusive(enabled)
        self._player.set_device_keepalive(self._exclusive and self._keepalive,
                                          self._wait_open)
        self._emit("player:signalpath", self.signal_path())

    def set_device_options(self, keepalive: bool | None = None,
                           wait_open: float | None = None) -> dict:
        """Ajustes finos del dispositivo (solo activos en modo exclusivo)."""
        if keepalive is not None:
            self._keepalive = bool(keepalive)
        if wait_open is not None:
            self._wait_open = max(0.0, min(5.0, float(wait_open)))
        self._player.set_device_keepalive(self._exclusive and self._keepalive,
                                          self._wait_open)
        return {"keepalive": self._keepalive, "wait_open": self._wait_open}

    def set_limiter(self, enabled: bool) -> dict:
        """Limitador de picos al final de la cadena (red de seguridad del DSP)."""
        self._limiter = bool(enabled)
        self._rebuild_af()
        return {"limiter": self._limiter}

    def set_listening_mode(self, enabled: bool) -> dict:
        """Modo Escucha: da preferencia de CPU al audio mientras suena música."""
        self._listening_mode = bool(enabled)
        self._apply_listening_mode(self.is_playing())
        return {"listening_mode": self._listening_mode}

    def is_playing(self) -> bool:
        try:
            return not bool(self._player._mpv.pause)
        except Exception:  # execpt
            return False

    def _apply_listening_mode(self, playing: bool) -> None:
        """Sube la prioridad del audio y baja la de los trabajos de fondo.

        No aplaza ni bloquea tareas: solo cede el turno de CPU, así que un
        análisis o un lote sigue avanzando mientras escuchas, sin competir con la
        reproducción. Se revierte al pausar.
        """
        from hifihub import win_console

        active = self._listening_mode and playing
        win_console.set_background_children(active)
        win_console.set_process_priority(active)

    def set_audio_device(self, device: str) -> dict:
        """Fija el dispositivo de salida, comprobando antes que exista.

        Windows puede REGENERAR los identificadores de los endpoints de audio (lo
        hace, por ejemplo, en algunas actualizaciones). Si la app guardó un
        dispositivo concreto y ese id desaparece, mpv falla con «Failed to
        initialize audio driver» y la reproducción se queda MUDA sin explicación.
        Aquí se detecta y se cae a `auto`, avisando a la interfaz.

        A propósito NO se sobrescribe la preferencia guardada, esto para evitar e.l origen del problema sea que el user haya desconectado del dispositivo de forma momentanea
        """
        device = device or "auto"
        if device != "auto" and not self._device_exists(device):
            self._emit("player:device-missing", {
                "requested": device,
                "devices": self.audio_devices(),
            })
            device = "auto"
        self._player.set_audio_device(device)
        return {"device": device}

    def _device_exists(self, name: str) -> bool:
        try:
            return any(d.get("name") == name for d in self.audio_devices())
        except Exception:  # noqa: BLE001 — si no se puede consultar, no bloquear
            return True

    def audio_devices(self) -> list[dict]:
        return self._player.list_audio_devices()

    # --- DSP y signal path ------------------------------------------------

    def set_eq(self, gains: list[float] | None = None, enabled: bool | None = None,
               freqs: list[float] | None = None) -> dict:
        if gains is not None:
            self._eq.set_gains(gains)
        if freqs is not None:
            # Lista vacía = restaurar las frecuencias ISO por defecto.
            self._eq.set_freqs(freqs or None)
        if enabled is not None:
            self._eq.enabled = bool(enabled)
        self._rebuild_af()
        return self._eq.state()

    def set_eq_solo(self, band: int | None) -> dict:
        """Aísla una banda del EQ para afinarla de oído (None = desactivar)."""
        self._solo_band = int(band) if band is not None else None
        self._rebuild_af()
        return {"solo": self._solo_band}

    def eq_state(self) -> dict:
        return self._eq.state()

    def set_headphone(self, name: str | None) -> dict:
        """Activa la corrección AutoEq de un auricular (None = desactivar)."""
        self._headphone = name or None
        self._rebuild_af()
        return {"headphone": self._headphone}

    def set_crossfeed(self, strength: float) -> dict:
        """Crossfeed para auriculares (0 = off, 0..1). Reduce la fatiga de las
        mezclas de estéreo extremo simulando escucha en sala."""
        self._crossfeed = max(0.0, min(1.0, float(strength)))
        self._rebuild_af()
        return {"crossfeed": self._crossfeed}

    def _rebuild_af(self) -> None:
        """Construye la cadena DSP completa en un único graph lavfi.

        Orden (importa): corrección del transductor -> EQ del usuario (+ su
        auto-preamp anti-clipping) -> convolución -> crossfeed (espacial) ->
        limitador (última red de seguridad, si está activo).
        """
        from hifihub.player.headphones import af_fragment_for

        fragments: list[str] = []
        hp = af_fragment_for(self._headphone)
        if hp:
            fragments.append(hp)

        eq = self._eq.af_fragment(solo=self._solo_band)
        if eq:
            fragments.append(eq)
            # Compensa la ganancia del EQ para no recortar.
            preamp = self._eq.auto_preamp_db
            if preamp:
                fragments.append(f"volume={preamp:g}dB")

        post: list[str] = []
        if self._crossfeed > 0:
            post.append(f"crossfeed=strength={self._crossfeed:g}")

        # El limitador solo aporta si hay algo que pueda pasarse de 0 dBFS.
        if self._limiter and (fragments or post or self._convolution):
            post.append("alimiter=limit=0.97:level=disabled")

        graph = self._build_graph(fragments, post)
        self._player._mpv["af"] = f"lavfi=[{graph}]" if graph else ""
        self._emit("player:signalpath", self.signal_path())

    def _build_graph(self, pre: list[str], post: list[str]) -> str:
        """Une la cadena en un graph de lavfi, insertando la convolución si la hay.

        `afir` toma DOS entradas (señal + impulso), así que no se puede encadenar
        con comas como el resto: hay que etiquetar la señal, traer el IR con
        `amovie` y unirlos. De ahí que el graph se construya aquí y no con un
        simple join.
        """
        conv_prefix = conv_filter = ""
        if self._convolution:
            try:
                from hifihub.player.convolution import af_fragment_for

                conv_prefix, conv_filter = af_fragment_for(self._convolution)
            except Exception:  # un IR ilegible no debe cortar el audio
                conv_prefix = conv_filter = ""

        if not conv_filter:
            return ",".join(pre + post)

        head = ",".join(pre) if pre else "anull"
        tail = ("," + ",".join(post)) if post else ""
        return f"{head}[main];{conv_prefix};[main][ir]{conv_filter}{tail}"

    def set_convolution(self, name: str | None) -> dict:
        """Activa la convolución con un impulso de la carpeta del usuario."""
        from hifihub.player.convolution import find_impulse

        path = find_impulse(name) if name else None
        self._convolution = str(path) if path else None
        self._rebuild_af()
        return {"convolution": Path(self._convolution).stem if self._convolution else None}

    def signal_path(self) -> dict[str, Any]:
        """Cadena real de reproducción, estilo Roon."""
        track = self.current_track()
        stages: list[dict[str, str]] = []

        if track:
            src = (track.get("codec") or "?").upper()
            if track.get("sample_rate"):
                src += f" {track['sample_rate'] / 1000:g} kHz"
            if track.get("bit_depth"):
                src += f" {track['bit_depth']}-bit"
            kind = "lossless" if track.get("lossless") else "lossy"
            stages.append({"stage": "Fuente", "detail": src, "kind": kind})

        stages.append({"stage": "Decodificación", "detail": "libmpv (FFmpeg)", "kind": "neutral"})

        rg_on = self._replaygain_mode not in ("", "no", None)
        if rg_on:
            detail = f"modo {self._replaygain_mode}"
            if self._replaygain_preamp:
                detail += f", preamp {self._replaygain_preamp:+g} dB"
            stages.append({"stage": "ReplayGain", "detail": detail, "kind": "adjust"})
        if self._headphone:
            stages.append({"stage": "Corrección de auriculares",
                           "detail": f"AutoEq: {self._headphone}", "kind": "dsp"})
        if self._eq.enabled:
            detail = "firequalizer"
            if self._solo_band is not None:
                detail += f" · banda {self._solo_band + 1} aislada"
            preamp = self._eq.auto_preamp_db
            if preamp:
                detail += f" · auto-preamp {preamp:+g} dB"
            stages.append({"stage": "EQ 10 bandas", "detail": detail, "kind": "dsp"})
        if self._convolution:
            stages.append({"stage": "Convolución (IR)",
                           "detail": Path(self._convolution).stem, "kind": "dsp"})
        if self._crossfeed > 0:
            stages.append({"stage": "Crossfeed", "detail": f"intensidad {self._crossfeed:g}", "kind": "dsp"})
        if self._volume != 100:
            stages.append({"stage": "Volumen", "detail": f"{self._volume:g}%", "kind": "adjust"})

        dsp_on = (self._eq.enabled or bool(self._headphone) or self._crossfeed > 0
                  or bool(self._convolution))
        if self._limiter and dsp_on:
            stages.append({"stage": "Limitador", "detail": "picos a −0.3 dBFS", "kind": "adjust"})

        out = "WASAPI exclusivo" if self._exclusive else "WASAPI compartido (mezclador de Windows)"
        dev_rate = None
        try:
            params = self._player._mpv.audio_out_params or {}
            dev_rate = params.get("samplerate")
            if dev_rate:
                out += f" · {dev_rate / 1000:g} kHz"
        except Exception:  # noqa: BLE001
            pass
        if self._exclusive and self._keepalive:
            out += " · DAC activo"
        stages.append({"stage": "Salida", "detail": out, "kind": "neutral"})

        
        resampling = None
        src_rate = (track or {}).get("sample_rate")
        if src_rate and dev_rate and int(src_rate) != int(dev_rate):
            resampling = {
                "from": int(src_rate), "to": int(dev_rate),
                "by": "el mezclador de Windows" if not self._exclusive else "la salida",
            }
            stages.append({
                "stage": "Remuestreo",
                "detail": f"{src_rate / 1000:g} → {dev_rate / 1000:g} kHz"
                          + ("" if self._exclusive else " (mezclador de Windows)"),
                "kind": "resample",
            })

        bit_perfect = (
            not dsp_on and not rg_on and self._volume == 100 and self._exclusive
            and resampling is None
        )
        quality = "bit-perfect" if bit_perfect else ("dsp" if dsp_on else "ajustada")
        return {"stages": stages, "bit_perfect": bit_perfect, "quality": quality,
                "resampling": resampling}

    def current_track(self) -> dict | None:
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    def get_queue(self) -> dict:
        return {
            "queue": self._queue,
            "index": self._index,
            "shuffle": self._shuffle,
            "repeat": self._repeat,
            "volume": self._volume,
        }

    def terminate(self) -> None:
        self._maybe_scrobble()
        if self._smtc is not None:
            self._smtc.close()
        self._player.terminate()

    # --- Internos -------------------------------------------------------

    def _apply_shuffle(self, keep: int) -> None:
        current = self._queue[keep]
        rest = [t for i, t in enumerate(self._queue) if i != keep]
        random.shuffle(rest)
        self._queue = [current] + rest

    def _emit(self, channel: str, payload: dict) -> None:
        if self._on_event is not None:
            self._on_event(channel, payload)

    def neighbours(self) -> dict[str, dict | None]:
        """Pista anterior y siguiente en la cola, para mostrarlas en el reproductor.

        El orden de `_queue` es el mismo que el de la playlist de mpv (al activar
        aleatorio se reordena la cola y se recarga), así que los vecinos son
        index±1. Con repetir-todo la cola da la vuelta, igual que `loop-playlist`.
        """
        n = len(self._queue)
        if n == 0 or not (0 <= self._index < n):
            return {"prev": None, "next": None}
        wrap = self._repeat == REPEAT_ALL

        def at(i: int) -> dict | None:
            if i < 0 or i >= n:
                if not wrap or n < 2:
                    return None
                i %= n
            t = self._queue[i]
            return {"id": t.get("id"), "title": t.get("title"),
                    "artist": t.get("artist"), "index": i}

        return {"prev": at(self._index - 1), "next": at(self._index + 1)}

    def _emit_neighbours(self) -> None:
        self._emit("player:neighbours", self.neighbours())

    def _emit_track(self) -> None:
        track = self.current_track()
        if track:
            self._emit("player:track", {"track": track, "index": self._index,
                                        "total": len(self._queue),
                                        **self.neighbours()})
            self._notify_now_playing(track)

    def _notify_now_playing(self, track: dict[str, Any]) -> None:
        # Reinicia el cronómetro de scrobble para la nueva pista.
        self._played_track = track
        self._played_started = time.monotonic()
        self._played_secs = 0.0
        if self._smtc is not None:
            self._smtc.update_now_playing(
                track.get("title"), track.get("artist"),
                track.get("album"), track.get("cover_path"),
            )
            self._smtc.set_playing(True)
        if self._scrobbler is not None and getattr(self._scrobbler, "enabled", False):
            try:
                self._scrobbler.update_now_playing(
                    track.get("artist") or "", track.get("title") or "", track.get("album")
                )
            except Exception:  # noqa: BLE001
                pass

    def _maybe_scrobble(self) -> None:
        """scrobble si sonó >50% de la pista o >4 minutos."""
        track = self._played_track
        if not track or self._scrobbler is None or not getattr(self._scrobbler, "enabled", False):
            return
        elapsed = time.monotonic() - self._played_started
        duration = track.get("duration") or 0
        if elapsed >= 240 or (duration and elapsed >= duration * 0.5):
            try:
                self._scrobbler.scrobble(
                    track.get("artist") or "", track.get("title") or "", track.get("album")
                )
            except Exception:  # noqa: BLE001
                pass
        self._played_track = None

    def _on_mpv_event(self, name: str, value: Any) -> None:
        if name == "time-pos" and value is not None:
            sec = int(value)
            if sec != self._last_pos:  # throttle a 1 Hz
                self._last_pos = sec
                self._emit("player:position", {"position": value})
        elif name == "duration" and value:
            self._emit("player:position", {"duration": value})
        elif name == "pause":
            playing = not bool(value)
            if self._smtc is not None:
                self._smtc.set_playing(playing)
            self._apply_listening_mode(playing)
            self._emit("player:state", {"playing": playing})
        elif name == "playlist-pos":
            # Tras restore(): índices previos al pedido son el valor inicial de un mpv
            # recién creado, no una pista que suene. Si la pedida fallara y mpv saltara
            # a la siguiente (índice mayor), se acepta: la marca nunca se queda puesta.
            if self._await_pos is not None:
                if int(value) < self._await_pos:
                    return
                self._await_pos = None
            # Eco de move_in_queue: sigue sonando la misma pista, solo cambió su sitio.
            # mpv puede REPETIR el aviso con el mismo valor (medido), así que la marca
            # se mantiene hasta que llega otro índice, no se consume con el primero.
            if self._expect_pos is not None:
                if int(value) == self._expect_pos:
                    return
                self._expect_pos = None
            # Duplicado del aviso de la pista recién retomada: ya se anunció una vez.
            if (self._resume_id is not None and self._resume_announced
                    and 0 <= int(value) < len(self._queue)
                    and self._queue[int(value)].get("id") == self._resume_id):
                return
            # Antes de cambiar de pista, decidir si la anterior se scrobblea.
            self._maybe_scrobble()
            self._index = int(value)
            track = self.current_track()
            if track:
                if track.get("id") and track["id"] != self._resume_id:
                    self.lib.mark_played(track["id"])
                if track.get("id") is not None and track.get("id") == self._resume_id:
                    self._resume_announced = True   # retomada: se anuncia sin contarla
                else:
                    self._resume_id = None          # ya suena otra: retomar quedó atrás
                self._emit_track()
        elif name == "end-file":
            # Solo un final NATURAL (EOF) puede cerrar la cola. Al cargar otra lista
            # o al hacer stop(), mpv corta el archivo anterior con motivo ABORTED: si
            # la nueva lista empezaba en su última pista por ejemplo al presionar reproducir ahora»
            # ese corte se leía como «terminó todo» y la interfaz quedaba en pausa aun con el audio sonando. Era un error visual.
            # ya que mpv no avanza ni vuelve a avisar.
            if getattr(value, "reason", END_EOF) not in END_FINAL:
                return
            # Fin natural de toda la cola - notificar parada.
            if self._index >= len(self._queue) - 1 and self._repeat == REPEAT_OFF:
                self._emit("player:state", {"playing": False, "ended": True})
        elif name == "device-busy":
            # Otra app tiene el dispositivo en exclusiva - mpv reintenta en silencio.
            self._emit("player:device-busy", {"exclusive": self._exclusive})
