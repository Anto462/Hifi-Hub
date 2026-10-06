"""Registro y orquestación de fuentes de descarga.

Cada fuente vive aislada en su módulo (`youtube.py`, `archive_org.py`,
`spotify.py`) y ninguna importa a otra. Este paquete es el ÚNICO sitio que las
conoce a todas, y decide de dónde se descarga según lo que el usuario haya
seleccionado en la UI:

- **Solo una fuente seleccionada** -> se usa esa sí o sí. Si la canción no está
  ahí, falla con un mensaje claro (no se cae a la otra a escondidas).
- **Las dos seleccionadas** -> se busca en ambas y gana la de MAYOR CALIDAD REAL,
  con la regla honesta de `multisource`: no se cambia de fuente por una mejora
  insignificante.

Spotify no es una fuente de audio (DRM); es un CATÁLOGO: resuelve qué canciones
son y el audio se baja de las fuentes de arriba.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hifihub.engine.sources import archive_org, spotify, youtube
from hifihub.engine.sources.base import Candidate, SourceError

__all__ = [
    "AUDIO_SOURCES", "ALL_SOURCES", "DEFAULT_SOURCES", "Candidate", "SourceError",
    "Resolution", "PostProcess", "parse_selection", "source_label", "resolve",
    "resolve_track", "download_candidate", "archive_org", "spotify", "youtube",
]

# Fuentes que entregan bytes de audio.
AUDIO_SOURCES: tuple[str, ...] = (youtube.NAME, archive_org.NAME)
# Todas las lanes, incluida la de solo-metadatos.
ALL_SOURCES: tuple[str, ...] = (*AUDIO_SOURCES, spotify.NAME)
DEFAULT_SOURCES = ",".join(AUDIO_SOURCES)

_LABELS = {
    youtube.NAME: youtube.LABEL,
    archive_org.NAME: archive_org.LABEL,
    spotify.NAME: spotify.LABEL,
}

# Alias tolerantes para lo que pueda llegar de la config o la UI.
_ALIASES = {
    "yt": youtube.NAME, "youtube": youtube.NAME, "music": youtube.NAME,
    "archive": archive_org.NAME, "archive.org": archive_org.NAME,
    "ia": archive_org.NAME, "internetarchive": archive_org.NAME,
}


def source_label(name: str) -> str:
    return _LABELS.get(name, name)


def parse_selection(value: str | list[str] | None) -> list[str]:
    """Normaliza la selección de fuentes de la UI/config a nombres válidos.

    Si queda vacía se devuelven todas las de audio: es mejor descargar de algún
    sitio que no descargar por una config corrupta.
    """
    if isinstance(value, str):
        raw = [p.strip() for p in value.split(",")]
    else:
        raw = [str(p).strip() for p in (value or [])]
    out: list[str] = []
    for item in raw:
        name = _ALIASES.get(item.lower(), item.lower())
        if name in AUDIO_SOURCES and name not in out:
            out.append(name)
    return out or list(AUDIO_SOURCES)


# --- Resultado de la resolución ---------------------------------------------

@dataclass
class Resolution:
    """Qué hacer con lo que pidió el usuario.

    - kind="download": hay un candidato elegido y se puede descargar ya.
    - kind="tracks": la entrada era un catálogo (playlist/álbum de Spotify) y hay
      que resolver y descargar cada canción por separado.
    """
    kind: str
    chosen: Candidate | None = None
    candidates: list[Candidate] = field(default_factory=list)
    wanted: list[spotify.WantedTrack] = field(default_factory=list)
    origin: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PostProcess:
    """Postprocesado común a TODAS las fuentes (idéntico al de las descargas)."""
    enrich: bool = True
    acoustid_key: str = ""
    analyze_bpm_key: bool = False
    replaygain: bool = True


# --- Elección honesta entre candidatos --------------------------------------

def choose_best(candidates: list[Candidate]) -> Candidate | None:
    """Ordena por calidad real y aplica la regla de mejora significativa.

    Si el candidato original (lo que pidió el usuario) no es superado de forma
    clara, se conserva: evita saltar de fuente por diferencias irrelevantes.
    """
    if not candidates:
        return None
    from hifihub.engine.multisource import _is_meaningfully_better

    ordered = sorted(candidates, key=lambda c: c.rank(), reverse=True)
    original = next((c for c in ordered if c.is_original_input), None)
    best = ordered[0]
    if original is not None and best is not original:
        if not _is_meaningfully_better(best.quality, original.quality):
            original.notes.append("Se conserva el original: sin mejora significativa.")
            return original
    return best


# --- Resolución --------------------------------------------------------------

def resolve_track(wanted: spotify.WantedTrack, *, selected: list[str],
                  progress: Callable[[str], None] | None = None) -> Resolution:
    """Busca UNA canción en las fuentes seleccionadas y elige la mejor."""
    cands: list[Candidate] = []
    errors: list[str] = []

    for name in selected:
        try:
            if name == youtube.NAME:
                cands += youtube.find_track(
                    wanted.title, wanted.artist or "", wanted.duration,
                    progress=progress)
            elif name == archive_org.NAME:
                cands += archive_org.find_track(
                    wanted.title, wanted.artist or "", wanted.duration,
                    progress=progress)
        except SourceError as e:
            # Una fuente caída no invalida a la otra.
            errors.append(f"{source_label(name)}: {e}")

    chosen = choose_best(cands)
    if chosen is None:
        detail = (" " + " · ".join(errors)) if errors else ""
        raise SourceError(
            f"No se encontró «{wanted.query or wanted.title}» en "
            f"{' ni '.join(source_label(s) for s in selected)}.{detail}"
        )
    return Resolution(kind="download", chosen=chosen, candidates=cands,
                      notes=errors)


def resolve(text: str, *, selected: str | list[str],
            spotify_client_id: str = "", spotify_client_secret: str = "",
            progress: Callable[[str], None] | None = None) -> Resolution:
    """Punto de entrada: enlace o texto libre -> qué descargar y de dónde."""
    selected = parse_selection(selected)
    text = (text or "").strip()
    if not text:
        raise SourceError("No hay nada que descargar.")

    # 1) Spotify = catálogo. Devuelve la lista de canciones a resolver aparte.
    if spotify.is_spotify_url(text):
        if progress:
            progress("Spotify: leyendo metadatos…")
        wanted = spotify.resolve(text, client_id=spotify_client_id,
                                 client_secret=spotify_client_secret)
        if not wanted:
            raise SourceError("Spotify no devolvió ninguna canción.")
        if len(wanted) == 1:
            return resolve_track(wanted[0], selected=selected, progress=progress)
        return Resolution(kind="tracks", wanted=wanted, origin=spotify.NAME)

    # 2) Enlace directo de archive.org: el enlace explícito manda; se baja ese
    #    ítem completo (un concierto = un álbum).
    ident = archive_org.identifier_from_url(text)
    if ident:
        if progress:
            progress(f"archive.org: leyendo {ident}…")
        cand = archive_org.fetch_item(ident).to_candidate()
        cand.is_original_input = True
        return Resolution(kind="download", chosen=cand, candidates=[cand],
                          origin=archive_org.NAME)

    is_url = text.lower().startswith(("http://", "https://"))

    # 3) Ruta rápida: un enlace y SOLO YouTube seleccionado. No hace falta
    #    inspeccionar nada (no hay con qué comparar), así que se baja directo y
    #    la descarga no paga ni una petición extra respecto a antes.
    if is_url and selected == [youtube.NAME]:
        cand = Candidate(source=youtube.NAME, ref=text, title=text,
                         quality=_unknown_quality(), is_original_input=True)
        return Resolution(kind="download", chosen=cand, candidates=[cand],
                          origin=youtube.NAME)

    # 4) Un enlace con archive.org en juego: hay que identificar la canción para
    #    poder buscarla en la otra fuente.
    if is_url:
        return _resolve_youtube_url(text, selected, progress)

    # 5) Texto libre -> se busca la canción en las fuentes seleccionadas.
    return resolve_track(spotify.WantedTrack(title=text), selected=selected,
                         progress=progress)


def _resolve_youtube_url(url: str, selected: list[str],
                         progress: Callable[[str], None] | None) -> Resolution:
    """Un enlace de YouTube, respetando la selección de fuentes.

    El enlace se inspecciona siempre (aunque YouTube no esté seleccionado) porque
    es lo que identifica la canción; eso NO descarga nada.
    """
    if progress:
        progress("Analizando el enlace…")
    origin = youtube.candidate_for_url(url)
    if origin is None:
        # Sin metadatos no se puede buscar en otra fuente; si YouTube está
        # seleccionado se intenta bajar igual (el motor tiene su reintento).
        if youtube.NAME in selected:
            cand = Candidate(source=youtube.NAME, ref=url, title=url,
                             quality=_unknown_quality(), is_original_input=True)
            return Resolution(kind="download", chosen=cand, candidates=[cand],
                              origin=youtube.NAME)
        raise SourceError("No se pudo leer el enlace de YouTube para buscarlo en otra fuente.")

    cands: list[Candidate] = []
    if youtube.NAME in selected:
        cands.append(origin)

    if archive_org.NAME in selected:
        try:
            cands += archive_org.find_track(
                origin.title, origin.artist or "", origin.duration, progress=progress)
        except SourceError as e:
            if youtube.NAME not in selected:
                raise
            origin.notes.append(f"archive.org no respondió: {e}")

    chosen = choose_best(cands)
    if chosen is None:
        raise SourceError(
            f"«{origin.title}» no está en {source_label(archive_org.NAME)}. "
            "Activa también YouTube si quieres descargarla de ahí."
        )
    return Resolution(kind="download", chosen=chosen, candidates=cands,
                      origin=youtube.NAME)


def _unknown_quality():
    from hifihub.engine.sources.base import Quality

    return Quality(codec="?", abr=None, asr=None, ext="?", is_lossless=False)


# --- Descarga ----------------------------------------------------------------

def download_candidate(
    cand: Candidate,
    dest: Path,
    *,
    profile: Any = "purista",
    template: str = "flat",
    post: PostProcess | None = None,
    yt_job: dict[str, Any] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    stage: Callable[[str], None] | None = None,
) -> None:
    """Descarga el candidato elegido por su lane y aplica el postprocesado.

    Cada fuente se encarga de traer los bytes; el postprocesado (enriquecido,
    ReplayGain y alta en la biblioteca) es el MISMO para todas.
    """
    post = post or PostProcess()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    if cand.source == youtube.NAME:
        job = dict(yt_job or {})
        job.update(
            enrich=post.enrich, acoustid_key=post.acoustid_key,
            analyze_bpm_key=post.analyze_bpm_key, replaygain=post.replaygain,
        )
        youtube.download(cand.ref, dest, profile, template,
                         progress=progress, **job)
        return

    if cand.source == archive_org.NAME:
        _download_archive(cand, dest, post=post, progress=progress, stage=stage)
        return

    raise SourceError(f"Fuente desconocida: {cand.source}")


def _download_archive(cand: Candidate, dest: Path, *, post: PostProcess,
                      progress: Callable[[dict[str, Any]], None] | None,
                      stage: Callable[[str], None] | None) -> None:
    """archive.org: HTTP directo a un temporal y luego el postprocesado común.

    Se baja a una carpeta temporal DENTRO del destino (mismo volumen: mover es un
    rename instantáneo) y se entrega al importador, que es quien ya sabe
    organizar + enriquecer + ReplayGain (modo álbum) + biblioteca. Así la música
    de archive.org recibe exactamente el mismo tratamiento que la importada y la
    descargada, sin duplicar esa lógica.
    """
    from hifihub.engine.importer import import_files

    ident = cand.extra.get("identifier") or cand.ref.split("/", 1)[0]
    only_file = cand.extra.get("file")

    item = archive_org.fetch_item(ident)
    tracks = None
    if only_file:
        tracks = [t for t in item.tracks if t.name == only_file] or None

    tmp = Path(tempfile.mkdtemp(prefix=".hifihub-archive-", dir=str(dest)))
    try:
        files = archive_org.download_item(
            ident, tmp, tracks=tracks, item=item, progress=progress, stage=stage)
        if not files:
            raise SourceError("archive.org no devolvió ningún fichero.")
        if stage:
            stage("Postprocesando (metadatos, volumen, biblioteca)…")
        import_files(
            files, dest, move=True,
            enrich=post.enrich, replaygain=post.replaygain,
            convert_flac=False,                      # nunca recodificar
            acoustid_key=post.acoustid_key,
            analyze_bpm_key=post.analyze_bpm_key,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
