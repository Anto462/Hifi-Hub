"""Configuración persistente en guardado Local"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import tomli_w

from hifihub import paths

# Aca se detalla cada opcion de las diversas config alrededor de la app y el tipo de dato que usa.
@dataclass
class Config:
    # Carpeta raíz por defecto donde se guarda la música descargada
    download_dir: str = ""
    # Perfil de calidad por defecto: "purista" (nativo, sin recodificar) o "mp3"
    default_profile: str = "purista"
    # "Buscar mejor origen": antes de descargar, busca la misma grabación en
    # otras fuentes y usa la de mayor calidad real. Opt-in (añade latencia de red).
    multi_source: bool = False
    # Fuentes de audio habilitadas (CSV de "youtube" / "archive"). Con UNA sola,
    # se descarga sí o sí de esa; con las DOS, se compara y gana la de mayor
    # calidad real (archive.org ofrece algunos FLAC lossless). Ver engine/sources/.
    # Por defecto solo YouTube: buscar en archive.org son varias llamadas HTTP y
    # no debe ralentizar cada descarga sin que el usuario lo pida.
    download_sources: str = "youtube"
    # Credenciales PROPIAS de la Web API de Spotify (developer.spotify.com, gratis).
    # Spotify se usa solo como CATÁLOGO: aporta la lista de canciones y el audio se
    # descarga de YouTube/archive.org. Sin credenciales solo resuelve pistas sueltas.
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    # Comprobar al arrancar si hay versión nueva del motor de descargas (yt-dlp).
    # Solo avisa; instalar siempre requiere un clic del usuario. No andamos ocultando descargas pibes.
    auto_check_updates: bool = True
    # Cuántas canciones por página en la vista Canciones (15 / 25 / 50).
    songs_per_page: int = 15
    # Tarjetas por página en las rejillas de Álbumes y Artistas (15 / 25 / 50).
    # Independiente de `songs_per_page`: en una rejilla caben más que en una lista.
    grid_per_page: int = 25
    # Canal del motor de descargas (yt-dlp): "stable" (PyPI) o "nightly" (GitHub).
    # Nightly trae los arreglos recientes de YouTube (errores 403 / formato no
    # disponible) a cambio de ser menos estable. Opt-in; reversible.
    ytdlp_channel: str = "stable"
    # Navegador del que leer cookies ("" = desactivado). Ej: "firefox", "chrome", "edge". Ya no se usa.
    cookies_browser: str = ""
    # Plan B para cookies: ruta a un cookies.txt exportado (Chrome en Windows
    # bloquea la lectura directa con app-bound encryption). Tiene prioridad
    # sobre cookies_browser si ambos están configurados. Ya no se usa.
    cookies_file: str = ""
    # Clientes de reproductor de YouTube (avanzado) usados cuando hay sesión/cookies.
    # Con cuenta autenticada, el cliente "web" exige PO Tokens y falla; estos sirven
    # el audio sin ese requisito. Vacío = usar el default del motor.
    youtube_player_clients: str = "web_safari,mweb,ios,web_music"
    # Dispositivo de salida de audio ("auto" = default del sistema)
    audio_device: str = "auto"
    # Descargas simultáneas en modo lote
    batch_concurrency: int = 2
    # Pausa (segundos) antes de cada descarga; reduce la detección de bots de
    # YouTube en álbumes/lotes. 0 = sin pausa. Se aleatoriza [pacing, pacing*1.6].
    download_pacing: float = 0.0
    # "Biblioteca Enriquecida": limpiar títulos, incrustar portada y metadatos
    enrich_library: bool = True
    # Clave gratuita de AcoustID (https://acoustid.org/new-application). Sin ella
    # se omite la identificación por huella acústica, pero el resto funciona.
    acoustid_api_key: str = ""
    # WASAPI exclusivo en reproducción: el DAC recibe el stream sin pasar por el
    # mezclador de Windows (bit-perfect), a cambio de tomar el dispositivo en exclusiva. Se nota la mejora con el DAC y equipo de audio adecuado.
    wasapi_exclusive: bool = False
    # Ajustes del dispositivo que SOLO se aplican en modo exclusivo (en compartido
    # el mezclador de Windows ya mantiene el dispositivo abierto):
    #  - keepalive: envía silencio para que el DAC no cierre entre pistas/pausas
    #    (elimina el "clic" del relé y los primeros ms comidos al reanudar).
    #  - wait_open: margen en segundos tras abrir el dispositivo antes de sonar.
    device_keepalive: bool = True
    device_wait_open: float = 0.3
    # Limitador de picos al final de la cadena DSP. Opt-in: el auto-preamp del EQ
    # ya evita el clipping de forma transparente; el limitador altera los picos,
    # así que solo se activa si el usuario lo pide.
    limiter_enabled: bool = False
    # Corrección AutoEq del auricular en uso ("" = ninguna). Nombre del preset.
    headphone_correction: str = ""
    # Crossfeed para auriculares (0 = off, 0..1). Reduce fatiga en estéreo extremo.
    crossfeed: float = 0.0
    # Convolución por respuesta al impulso ("" = ninguna). Nombre del IR importado
    # en ~/.hifihub/impulses. Permite corrección de sala medida (p. ej. con REW).
    convolution: str = ""
    # "Modo Escucha": aplaza tareas pesadas (BPM, espectros, lotes) mientras suena
    # música, para que la CPU no compita con la reproducción.
    listening_mode: bool = False
    # "Volumen Uniforme": escribir tags ReplayGain (EBU R128, no destructivo) tras
    # cada descarga. El reproductor los aplica sin recodificar el audio.
    apply_replaygain: bool = True
    # Nivelación en REPRODUCCIÓN: "off" | "track" | "album".
    replaygain_mode: str = "track"
    # Preamp de ReplayGain en dB (compensa que RG2 nivela a -18 LUFS y deja las
    # pistas modernas "bajas"). +3 dB es un punto de partida cómodo.
    replaygain_preamp: float = 3.0
    # SponsorBlock Music Edition: cortar tramos no musicales (music_offtopic).
    # Off por defecto: re-muxea el archivo y depende de datos comunitarios.
    sponsorblock: bool = False
    # Analizar BPM y tonalidad tras enriquecer (costoso: decodifica la pista
    # entera). Off por defecto; útil sobre todo para DJs.
    analyze_bpm_key: bool = False
    # Scrobbling a Last.fm (opcional). Vacío = desactivado.
    lastfm_api_key: str = ""
    lastfm_api_secret: str = ""
    lastfm_session_key: str = ""

    def __post_init__(self) -> None:
        if not self.download_dir:
            self.download_dir = str(paths.default_music_dir())

    @classmethod
    def load(cls, file: Path | None = None) -> "Config":
        file = file or paths.CONFIG_FILE
        if not file.exists():
            return cls()
        with open(file, "rb") as f:
            data = tomllib.load(f)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, file: Path | None = None) -> Path:
        file = file or paths.CONFIG_FILE
        file.parent.mkdir(parents=True, exist_ok=True)
        with open(file, "wb") as f:
            tomli_w.dump(asdict(self), f)
        return file
