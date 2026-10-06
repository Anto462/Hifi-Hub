# Componentes de terceros

HiFi Hub se apoya en software y recursos creados por otras personas. Cada uno
conserva **su propia licencia**.

Este archivo existe para dar el crédito debido y para cumplir las obligaciones de
las licencias copyleft: **cuando se distribuye un binario GPL hay que ofrecer
también su código fuente**, y por eso cada entrada incluye el enlace a las
fuentes de la versión exacta que se usa aquí.

## Por qué este proyecto es GPL v3

El código propio de HiFi Hub se publica bajo **GPL v3** (ver `LICENSE.txt`). Aparte de buscar aportar a la comunidad de una manera gratuita varias piezas centrales usadas en herramientas son copyleft y obligan usar una licencia que se adapte a las suyas en este caso siendo **GPL v3** la acorde.

Esto significa que cualquiera puede usar, estudiar, modificar y redistribuir este
software, **siempre que mantenga la misma licencia y entregue el código fuente**.

---

## 1. Herramientas incluidas en `herramientas/`

Son ejecutables y librerías independientes. La aplicación los **invoca como
procesos externos** o los carga en tiempo de ejecución; no forman parte del
código fuente de este repositorio. Viajan comprimidos en `exe_tools.rar` y
`dlls.rar` netamente porque algunos superan el límite de 100 MB por archivo de GitHub.

### FFmpeg (`ffmpeg.exe`, `ffprobe.exe`)

Decodificación, conversión, filtros de audio (ecualizador, crossfeed, convolución)
y análisis de sonoridad.

| | |
|---|---|
| **Versión** | `N-125648-g7f6b35d6c8-20260717` |
| **Licencia** | **GPL v3 o posterior** — build con `--enable-gpl --enable-version3` |
| **Web** | https://ffmpeg.org |
| **Fuentes** | https://github.com/FFmpeg/FFmpeg (commit `7f6b35d6c8`) |
| **Build usado** | https://github.com/BtbN/FFmpeg-Builds |

> Este build **no** incluye `--enable-nonfree`, por lo que es redistribuible bajo
> los términos de la GPL. Incluye componentes solo-GPL (x264, x265, libvidstab),
> de ahí que el binario completo sea GPL y no LGPL.

### libmpv (`libmpv-2.dll`)

Decodificación gapless, ReplayGain, salida WASAPI y cadena
de filtros de audio.

| | |
|---|---|
| **Versión** | `v0.41.0-744-g304426c39` |
| **Licencia** | **GPL v2 o posterior** (el núcleo de mpv es LGPL v2.1+, pero este build enlaza componentes solo-GPL como x264 y x265) |
| **Web** | https://mpv.io |
| **Fuentes** | https://github.com/mpv-player/mpv |
| **Build usado** | https://github.com/shinchiro/mpv-winbuild-cmake |

### rsgain (`rsgain.exe`)

Cálculo de ReplayGain 2.0 / EBU R128 para la nivelación de volumen.
Incluye los perfiles de `herramientas/presets/`.

| | |
|---|---|
| **Versión** | `3.7` (con libebur128 1.2.6) |
| **Licencia** | **BSD 2-Clause** |
| **Web / Fuentes** | https://github.com/complexlogic/rsgain |

### Chromaprint (`fpcalc.exe`)

Huella acústica para identificar pistas mal etiquetadas (vía AcoustID).

| | |
|---|---|
| **Versión** | `1.6.0` |
| **Licencia** | **LGPL v2.1 o posterior** |
| **Web / Fuentes** | https://github.com/acoustid/chromaprint |

### Microsoft Visual C++ Runtime (`msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`)

Librerías de ejecución que necesitan los binarios anteriores.

| | |
|---|---|
| **Versión** | `14.44.35211.0` |
| **Licencia** | Microsoft Visual C++ Redistributable — redistribuible según los términos de Microsoft |
| **Web** | https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist |

---

## 2. Dependencias de Python

Declaradas en `pyproject.toml`. Se instalan desde PyPI; no se incluyen en este
repositorio.

| Paquete | Versión | Licencia |
|---|---|---|
| yt-dlp | 2026.7.4 | Unlicense (dominio público) |
| python-mpv | 1.0.8 | **GPL v2 o posterior** |
| mutagen | 1.48.1 | **GPL v2 o posterior** |
| pywebview | 6.2.1 | BSD |
| typer | 0.27.0 | MIT |
| rich | 15.0.0 | MIT |
| tomli-w | 1.2.0 | MIT |
| requests | 2.34.2 | Apache 2.0 |
| Pillow | 12.3.0 | MIT-CMU |
| musicbrainzngs | 0.7.1 | BSD |
| pyacoustid | 1.3.1 | MIT |
| NumPy | 2.4.6 | BSD-3-Clause |
| librosa | 0.11.0 | ISC |
| pythonnet | 3.1.0 | MIT |
| winsdk | 1.0.0b10 | MIT |

### Extras opcionales (`studio`)

Solo se usan si se instalan; la aplicación funciona sin ellos y lo indica con un
mensaje claro.

| Paquete | Versión | Licencia |
|---|---|---|
| pedalboard | 0.9.24 | **GPL v3** |
| matchering | 2.0.6 | **GPL v3** |

### Herramienta de empaquetado

| Paquete | Versión | Licencia |
|---|---|---|
| PyInstaller | 6.21.0 | GPL v2 **Permite excepción**: el ejecutable generado puede distribuirse bajo la licencia que elija el autor |

---

## 3. Tipografías

Incluidas en `hifihub/ui/web/fonts/` para que la interfaz funcione **sin conexión**.

| Familia | Archivos | Licencia | Origen |
|---|---|---|---|
| IBM Plex Sans | `ibm-plex-sans-400/600/700.woff2` | SIL Open Font License 1.1 | https://github.com/IBM/plex |
| IBM Plex Mono | `ibm-plex-mono-400/500.woff2` | SIL Open Font License 1.1 | https://github.com/IBM/plex |
| Anton | `anton-400.woff2` | SIL Open Font License 1.1 | https://fonts.google.com/specimen/Anton |
| Antonio | `antonio-700.woff2` | SIL Open Font License 1.1 | https://fonts.google.com/specimen/Antonio |

---

## 4. Iconografía

| Recurso | Archivos | Licencia | Origen |
|---|---|---|---|
| Phosphor Icons (peso Bold) | `icons/Phosphor-Bold.woff2`, `icons/phosphor-bold.css` | MIT | https://github.com/phosphor-icons/homepage |

---

## 5. Datos de corrección de auriculares

| Recurso | Ubicación | Licencia | Origen |
|---|---|---|---|
| Perfiles ParametricEQ de AutoEq | `hifihub/data/autoeq/*.txt` | MIT | https://github.com/jaakkopasanen/AutoEq |

Proyecto de Jaakko Pasanen. Las correcciones se generan a partir de mediciones de
distintas fuentes (Rtings, Auriculares Argentina, Kuulokenurkka, entre otras)
hacia el target Harman. Detalle en `hifihub/data/autoeq/ATTRIBUTION.md`.

---

## 6. Servicios y APIs externas consultadas

No se incluye código; la aplicación solamente consulta sus APIs públicas de forma
opcional y solo cuando el usuario lo activa.

| Servicio | Uso | Términos |
|---|---|---|
| MusicBrainz | Metadatos de álbumes y pistas | https://musicbrainz.org/doc/About/Data_License (CC0 / CC BY-NC-SA según el dato) |
| Cover Art Archive | Carátulas | https://coverartarchive.org |
| AcoustID | Identificación por huella acústica | https://acoustid.org |
| SponsorBlock | Tramos no musicales en vídeos | https://sponsor.ajay.app |
| Last.fm | Scrobbling (opcional) | https://www.last.fm/api/tos |

---

## Cómo obtener las fuentes de los componentes GPL

Si quieres el código fuente o prefieres descargar de forma manual cualquiera de los binarios GPL distribuidos en `herramientas/`, usa los enlaces agregados en las secciones de este documento cuando es especifica cada herramienta.
