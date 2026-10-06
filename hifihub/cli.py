"""CLI provisional. Se uso como interza hasta la creacion de la UI. Actual herramienta de automatización/scripting."""
# Imports
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from hifihub import __version__, profiles, templates
from hifihub.audio import inspect as audio_inspect
from hifihub.config import Config
from hifihub.engine.downloader import Downloader

app = typer.Typer(
    name="hifihub",
    help="HiFi Hub — Ecosistema de audio en máxima calidad.",
    no_args_is_help=True,
)
console = Console()

# Consolas Windows heredadas usan cp1252, no usar unicode en estas.
# degradar a '?' antes que abortar una descarga ya completada.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

# Proceso de progreso de descargas
def _progress(d: dict[str, Any]) -> None:
    if d.get("status") == "downloading":
        pct = d.get("_percent_str", "?").strip()
        speed = d.get("_speed_str", "?").strip()
        console.print(f"  descargando {pct} a {speed}", end="\r", highlight=False)
    elif d.get("status") == "finished":
        console.print()
        console.print("[green]descarga completa, postprocesando...[/green]")


def _resolve_dest(dir_opt: Optional[Path]) -> Path:
    return dir_opt if dir_opt is not None else Path(Config.load().download_dir)


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@app.command()
def audio(
    url: Annotated[str, typer.Argument(help="Enlace del video/pista/playlist")],
    dir: Annotated[Optional[Path], typer.Option("--dir", "-d", help="Carpeta raíz destino")] = None,
    profile: Annotated[str, typer.Option("--profile", "-p", help=f"Perfil: {', '.join(profiles.PRESETS)}")] = "",
    template: Annotated[str, typer.Option("--template", "-t", help=f"Organización: {', '.join(templates.TEMPLATES)}")] = "flat",
    sample_rate: Annotated[Optional[int], typer.Option("--sample-rate", help="Resamplear a N Hz (solo flac/alac; usa soxr)")] = None,
    bit_depth: Annotated[Optional[int], typer.Option("--bit-depth", help="Profundidad de bits 16/24 (solo flac/alac; 16 aplica dithering)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Mostrar qué se descargaría y dónde, sin descargar")] = False,
    enrich: Annotated[Optional[bool], typer.Option("--enrich/--no-enrich", help="Biblioteca Enriquecida: limpiar títulos, portada y metadatos")] = None,
    multi_source: Annotated[Optional[bool], typer.Option("--multi-source/--no-multi-source", help="Buscar la mejor calidad de la misma canción en otras fuentes")] = None,
    source: Annotated[str, typer.Option("--source", help="Fuentes: 'youtube', 'archive' o 'youtube,archive' (las dos = gana la de mayor calidad). Vacío = config")] = "",
    section: Annotated[Optional[list[str]], typer.Option("--section", "-s", help="Recorte HH:MM:SS-HH:MM:SS (repetible para varios tracks)")] = None,
    exact: Annotated[bool, typer.Option("--exact", help="Cortes exactos al segundo (recodifica el tramo; el default corta en keyframes sin recodificar)")] = False,
    split_chapters: Annotated[bool, typer.Option("--split-chapters", help="Dividir por capítulos del video (Util para DJ sets con tracklist)")] = False,
    sponsorblock: Annotated[Optional[bool], typer.Option("--sponsorblock/--no-sponsorblock", help="Cortar tramos no musicales (music_offtopic)")] = None,
) -> None:
    """Descarga solo audio. Por defecto perfil 'purista': stream nativo sin recodificar."""
    cfg = Config.load()
    prof = profiles.get_profile(profile or cfg.default_profile)
    dest = _resolve_dest(dir)
    do_enrich = cfg.enrich_library if enrich is None else enrich

    if (sample_rate or bit_depth):
        if not prof.recodes:
            console.print("[yellow]Aviso:[/yellow] resamplear no aplica al perfil 'purista' (no recodifica). Se ignora.")
        else:
            prof = prof.with_resample(sample_rate, bit_depth)

    dl = Downloader(progress=_progress)

    if dry_run:
        console.print(f"[bold]Dry-run[/bold] · perfil {prof.name} · plantilla {template}")
        plan = dl.plan_audio(url, dest, template)
        table = Table(title=f"{len(plan)} pista(s) — raíz {dest}")
        table.add_column("#", justify="right")
        table.add_column("Duración", justify="right")
        table.add_column("Ruta destino")
        for i, item in enumerate(plan, 1):
            rel = item.dest_path
            try:
                rel = str(Path(item.dest_path).relative_to(dest))
            except ValueError:
                pass
            table.add_row(str(i), _fmt_duration(item.duration), rel)
        console.print(table)
        return

    if prof.name == "mp3":
        console.print("[yellow]Aviso:[/yellow] convertir a MP3 recodifica y pierde calidad. "
                      "El perfil 'purista' conserva el audio original.")
    do_sb = cfg.sponsorblock if sponsorblock is None else sponsorblock
    if do_sb:
        console.print("[yellow]SponsorBlock:[/yellow] se cortarán tramos no musicales; "
                      "el archivo se re-muxea (deja de ser byte-idéntico al original).")
    # Fuentes: si entra archive.org en juego, la resolución y la descarga las
    # lleva el orquestador de `engine.sources` (cada fuente en su lane).
    from hifihub.engine import sources as src
    selected = src.parse_selection(source or cfg.download_sources)
    if selected != [src.youtube.NAME] or src.spotify.is_spotify_url(url) \
            or src.archive_org.is_archive_url(url):
        _download_via_sources(url, dest, prof, template, selected, cfg,
                              do_enrich, section)
        return

    do_multi = cfg.multi_source if multi_source is None else multi_source
    if do_multi and not section:
        url = _resolve_best_source(url) or url

    if section:
        from hifihub.engine.sections import parse_ranges
        ranges = parse_ranges(section)  # valida si hay rangos de segundos antes de descargar
        modo = "exactos (recodifica)" if exact else "en keyframes (sin recodificar; el inicio puede adelantarse)"
        console.print(f"[bold]Cortes {modo}:[/bold] {', '.join(r.label() for r in ranges)}")
    console.print(f"[bold]Perfil:[/bold] {prof.name}  [bold]Plantilla:[/bold] {template}  "
                  f"[bold]Enriquecido:[/bold] {'sí' if do_enrich else 'no'}  [bold]Destino:[/bold] {dest}")
    dl.download_audio(url, dest, prof, template, enrich=do_enrich,
                      acoustid_key=cfg.acoustid_api_key, analyze_bpm_key=cfg.analyze_bpm_key,
                      replaygain=cfg.apply_replaygain,
                      sections=section, exact_cuts=exact,
                      split_chapters=split_chapters, sponsorblock=do_sb,
                      cookies_browser=cfg.cookies_browser, cookies_file=cfg.cookies_file,
                      player_clients=cfg.youtube_player_clients)
    console.print("[bold green]OK Audio descargado.[/bold green]")


def _download_via_sources(url, dest, prof, template, selected, cfg, do_enrich,
                          section) -> None:
    """Descarga pasando por el orquestador de fuentes (YouTube / archive.org)."""
    from hifihub.engine import sources as src

    if section:
        console.print("[yellow]Aviso:[/yellow] los cortes solo aplican a YouTube; "
                      "se ignoran para archive.org.")
    console.print("[bold]Fuentes:[/bold] " +
                  ", ".join(src.source_label(s) for s in selected))
    post = src.PostProcess(
        enrich=do_enrich, acoustid_key=cfg.acoustid_api_key,
        analyze_bpm_key=cfg.analyze_bpm_key, replaygain=cfg.apply_replaygain,
    )
    try:
        res = src.resolve(
            url, selected=selected,
            spotify_client_id=cfg.spotify_client_id,
            spotify_client_secret=cfg.spotify_client_secret,
            progress=lambda m: console.print(f"  [dim]{m}[/dim]"),
        )
    except src.SourceError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e

    wanted = res.wanted if res.kind == "tracks" else []
    if wanted:
        console.print(f"[bold]Catálogo:[/bold] {len(wanted)} canción(es)")
        ok = 0
        for i, w in enumerate(wanted, 1):
            console.print(f"[{i}/{len(wanted)}] {w.query or w.title}")
            try:
                one = src.resolve_track(w, selected=selected)
                src.download_candidate(one.chosen, dest, profile=prof,
                                       template=template, post=post,
                                       progress=_progress)
                ok += 1
            except src.SourceError as e:
                console.print(f"   [yellow]sin resultado:[/yellow] {e}")
        console.print(f"[bold green]OK {ok}/{len(wanted)} descargadas.[/bold green]")
        return

    console.print(f"[bold]Elegido:[/bold] {res.chosen.describe()}")
    src.download_candidate(res.chosen, dest, profile=prof, template=template,
                           post=post, progress=_progress,
                           stage=lambda m: console.print(f"  [dim]{m}[/dim]"))
    console.print("[bold green]OK Audio descargado.[/bold green]")

# Buscar mejor fuente de Audio
def _resolve_best_source(url: str) -> Optional[str]:
    """Busca la mejor fuente, muestra la tabla y devuelve la URL ganadora."""
    from hifihub.engine.multisource import find_best_source

    cfg = Config.load()
    with console.status("Buscando mejores fuentes…") as status:
        cands = find_best_source(
            url, cookies_browser=cfg.cookies_browser, cookies_file=cfg.cookies_file,
            progress=lambda msg: status.update(msg),
        )
    if not cands:
        console.print("[yellow]No se pudo analizar; se usa el enlace original.[/yellow]")
        return None

    table = Table(title="Fuentes encontradas (mejor calidad primero)")
    table.add_column("★")
    table.add_column("Fuente")
    table.add_column("Calidad")
    table.add_column("Similitud", justify="right")
    table.add_column("Título")
    for i, c in enumerate(cands):
        mark = "▶" if i == 0 else ("·" if c.is_original_input else "")
        origen = c.source + (" (original)" if c.is_original_input else "")
        table.add_row(mark, origen, c.quality.label(),
                      f"{c.match_score:.0%}" if not c.is_original_input else "—",
                      c.title[:40], highlight=False)
    console.print(table)

    best = cands[0]
    if best.is_original_input:
        console.print("[green]El enlace original ya es la mejor calidad.[/green]")
        return None
    console.print(f"[bold green]Mejor fuente: {best.source}[/bold green] — {best.quality.label()}")
    return best.url


@app.command()
def video(
    url: Annotated[str, typer.Argument(help="Enlace del video")],
    dir: Annotated[Optional[Path], typer.Option("--dir", "-d", help="Carpeta destino")] = None,
) -> None:
    """Descarga video en HD (720p o superior)."""
    dest = _resolve_dest(dir)
    console.print(f"[bold]Destino:[/bold] {dest}")
    Downloader(progress=_progress).download_video(url, dest)
    console.print("[bold green]OK Video descargado.[/bold green]")


@app.command()
def inspect(
    file: Annotated[Path, typer.Argument(help="Archivo de audio a analizar")],
    spectral: Annotated[bool, typer.Option("--spectral", help="Análisis espectral: detectar falso hi-res (fuente lossy re-empaquetada)")] = False,
) -> None:
    """Muestra la calidad REAL de un archivo (codec, sample rate, bitrate) vía ffprobe."""
    try:
        info = audio_inspect.probe(file)
    except audio_inspect.ProbeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    table = Table(title=f"{file.name} — {info.quality_label()}")
    table.add_column("Propiedad")
    table.add_column("Valor")
    table.add_row("Codec", info.codec)
    table.add_row("Contenedor", info.container)
    table.add_row("Sample rate", f"{info.sample_rate} Hz ({info.sample_rate_khz} kHz)")
    table.add_row("Profundidad", f"{info.bit_depth}-bit" if info.bit_depth else "(no aplica)")
    table.add_row("Canales", str(info.channels))
    table.add_row("Bitrate", f"{info.bit_rate_kbps} kbps" if info.bit_rate_kbps else "(lossless/desconocido)")
    table.add_row("Duración", _fmt_duration(info.duration))
    table.add_row("Lossless", "sí" if info.lossless else "no")
    console.print(table)

    if spectral:
        from hifihub.audio.spectrum import SpectrumError, analyze

        try:
            a = analyze(file)
        except SpectrumError as e:
            console.print(f"[red]Análisis espectral falló:[/red] {e}")
            raise typer.Exit(1)
        color = "yellow" if a.suspicious else "green"
        console.print(f"[bold]Corte espectral:[/bold] ~{a.cutoff_khz} kHz de {a.nyquist_hz / 1000:g} kHz "
                      f"(ratio {a.ratio})")
        console.print(f"[{color}]{a.verdict}[/{color}]")


@app.command("profiles")
def list_profiles() -> None:
    """Lista los perfiles de calidad disponibles."""
    table = Table(title="Perfiles de audio")
    table.add_column("Nombre")
    table.add_column("Recodifica")
    table.add_column("Descripción")
    for name, prof in profiles.PRESETS.items():
        table.add_row(name, "sí" if prof.recodes else "no", prof.description)
    console.print(table)

# Para abrir la UI
@app.command()
def ui(
    debug: Annotated[bool, typer.Option("--debug", help="Abrir con devtools del WebView")] = False,
) -> None:
    """Abre la aplicación de escritorio (pywebview)."""
    from hifihub.ui.app import run

    run(debug=debug)

# Logica para importar la musica y opciones
@app.command("import")
def import_music(
    inputs: Annotated[list[Path], typer.Argument(help="Archivos de audio o carpetas a importar")],
    dir: Annotated[Optional[Path], typer.Option("--dir", "-d", help="Carpeta destino (por defecto la de la biblioteca)")] = None,
    move: Annotated[bool, typer.Option("--move", help="Mover en vez de copiar (borra el original)")] = False,
    enrich: Annotated[Optional[bool], typer.Option("--enrich/--no-enrich", help="Identificar y enriquecer metadatos")] = None,
    replaygain: Annotated[Optional[bool], typer.Option("--replaygain/--no-replaygain", help="Escribir tags ReplayGain")] = None,
    convert_flac: Annotated[bool, typer.Option("--to-flac", help="Convertir a FLAC (por defecto conserva el formato)")] = False,
) -> None:
    """Importa tu propia música (FLAC, rips de CD, compras) a la biblioteca."""
    from hifihub.engine.importer import collect_audio, import_files

    cfg = Config.load()
    dest = dir if dir is not None else Path(cfg.download_dir)
    files = collect_audio(inputs)
    if not files:
        console.print("[yellow]No se encontraron archivos de audio.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[bold]Importando {len(files)} archivo(s)[/bold] a {dest}"
                  f"{' (mover)' if move else ' (copiar)'}")

    def on_event(ch: str, p: dict[str, Any]) -> None:
        if ch == "import:item":
            if p.get("ok"):
                mark = "omitido" if p.get("skipped") else "ok"
                console.print(f"  [{p['done']}/{p['total']}] [green]{mark}[/green] {Path(p['dst']).name}", highlight=False)
            else:
                console.print(f"  [{p['done']}/{p['total']}] [red]error[/red] {Path(p['src']).name}: {p.get('error','')[:60]}", highlight=False)

    report = import_files(
        inputs, dest, move=move,
        enrich=cfg.enrich_library if enrich is None else enrich,
        replaygain=cfg.apply_replaygain if replaygain is None else replaygain,
        convert_flac=convert_flac, acoustid_key=cfg.acoustid_api_key,
        analyze_bpm_key=cfg.analyze_bpm_key,
        on_event=on_event,
    )
    console.print(f"[bold green]{report.imported} importadas · {report.skipped} omitidas · "
                  f"{report.errors} errores.[/bold green] Los originales "
                  f"{'se movieron' if move else 'no se tocaron'}.")

# Logica para scan a una carpeta en busca de archivos de audio mp3, wav, flac, etc.
@app.command()
def scan(
    dir: Annotated[Optional[Path], typer.Option("--dir", "-d", help="Carpeta a escanear")] = None,
) -> None:
    """Escanea una carpeta e indexa las pistas en la biblioteca."""
    from hifihub.library import scanner
    from hifihub.library.db import Library

    root = dir if dir is not None else Path(Config.load().download_dir)
    lib = Library()
    console.print(f"[bold]Escaneando:[/bold] {root}")
    result = scanner.scan([root], lib)
    stats = lib.stats()
    console.print(f"[green]+{result.added} nuevas · {result.updated} actualizadas · "
                  f"{result.removed} eliminadas · {result.errors} errores[/green]")
    console.print(f"Biblioteca: {stats['tracks']} pistas · {stats['albums']} álbumes · "
                  f"{stats['artists']} artistas")
    lib.close()

# Decarga en lote
@app.command()
def batch(
    file: Annotated[Path, typer.Argument(help="Archivo .txt: un enlace por línea, # comentarios")],
    dir: Annotated[Optional[Path], typer.Option("--dir", "-d", help="Carpeta raíz destino")] = None,
    profile: Annotated[str, typer.Option("--profile", "-p")] = "",
    template: Annotated[str, typer.Option("--template", "-t")] = "flat",
    enrich: Annotated[Optional[bool], typer.Option("--enrich/--no-enrich")] = None,
    concurrency: Annotated[Optional[int], typer.Option("--jobs", "-j", help="Descargas simultáneas")] = None,
) -> None:
    """Procesa un lote de enlaces. Omite lo ya descargado (download_archive)."""
    from hifihub.batch.queue import parse_links_file, run_batch
    from hifihub.batch.registry import Registry

    cfg = Config.load()
    urls = parse_links_file(file)
    if not urls:
        console.print("[yellow]El archivo no contiene enlaces.[/yellow]")
        raise typer.Exit(1)
    dest = _resolve_dest(dir)
    do_enrich = cfg.enrich_library if enrich is None else enrich

    console.print(f"[bold]{len(urls)} enlaces[/bold] · perfil {profile or cfg.default_profile} · destino {dest}")

    def on_event(channel: str, p: dict[str, Any]) -> None:
        if channel == "batch:item":
            mark = "[green]ok[/green]" if p["status"] == "ok" else f"[red]error[/red] {p['error'] or ''}"
            console.print(f"  [{p['done']}/{p['total']}] {p['url']} -> {mark}", highlight=False)

    report = run_batch(
        urls, dest,
        profile=profile or cfg.default_profile, template=template,
        enrich=do_enrich, acoustid_key=cfg.acoustid_api_key,
        replaygain=cfg.apply_replaygain,
        cookies_browser=cfg.cookies_browser, cookies_file=cfg.cookies_file,
        player_clients=cfg.youtube_player_clients,
        concurrency=concurrency or cfg.batch_concurrency,
        on_event=on_event, registry=Registry(),
    )
    color = "green" if report.failed == 0 else "yellow"
    console.print(f"[bold {color}]Lote terminado: {report.ok} ok · {report.failed} con error "
                  f"de {report.total}.[/bold {color}]")
    if report.failed:
        raise typer.Exit(1)

# Analiza las canciones descargadas o importadas para saber su bpm, tonos, camelot, etc
@app.command()
def analyze(
    files: Annotated[list[Path], typer.Argument(help="Archivos o carpeta a analizar")],
) -> None:
    """Detecta BPM y tonalidad y los escribe como tags (BPM/INITIALKEY)."""
    from hifihub.audio.analysis import analyze_and_tag
    from hifihub.library.scanner import iter_audio_files

    targets: list[Path] = []
    for f in files:
        targets.extend(iter_audio_files(f) if f.is_dir() else [f])
    if not targets:
        console.print("[yellow]No hay archivos de audio.[/yellow]")
        raise typer.Exit(1)

    table = Table(title="Análisis musical")
    table.add_column("Archivo")
    table.add_column("BPM", justify="right")
    table.add_column("Tonalidad")
    table.add_column("Camelot")
    with console.status("Analizando…"):
        for f in targets:
            try:
                r = analyze_and_tag(f)
                table.add_row(f.name, str(r.bpm), r.key, r.key_camelot or "-")
            except Exception as e:  # noqa: BLE001
                table.add_row(f.name, "[red]error[/red]", str(e)[:30], "-")
    console.print(table)

# Para procesar el audio con los "filtros" que ofrece el reproductor, crea copias del audio original con los ajsutes suando pedalboard.
@app.command()
def studio(
    files: Annotated[list[Path], typer.Argument(help="Archivos a procesar (o una carpeta)")],
    preset: Annotated[str, typer.Option("--preset", "-P", help="Preset de efectos")] = "claridad",
    out_dir: Annotated[Optional[Path], typer.Option("--out-dir", help="Carpeta de salida (por defecto junto al original)")] = None,
) -> None:
    """Procesa copias con calidad de estudio (pedalboard). NUNCA toca el original."""
    from hifihub.library.scanner import iter_audio_files
    from hifihub.studio import presets
    from hifihub.studio.chain import AVAILABLE, StudioError, default_output_path, process_file

    if not AVAILABLE:
        console.print("[red]Falta el extra 'studio'.[/red] Instala con: pip install -e \".[studio]\"")
        raise typer.Exit(1)
    try:
        chain = presets.get_preset_chain(preset)
    except KeyError:
        console.print(f"[red]Preset desconocido.[/red] Opciones: {', '.join(p['name'] for p in presets.list_presets())}")
        raise typer.Exit(1)

    targets: list[Path] = []
    for f in files:
        targets.extend(iter_audio_files(f) if f.is_dir() else [f])
    if not targets:
        console.print("[yellow]No hay archivos de audio.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold]Preset:[/bold] {preset} · {len(targets)} archivo(s)")
    ok = 0
    for src in targets:
        dst = default_output_path(src, out_dir)
        try:
            process_file(src, dst, chain)
            console.print(f"  [green]OK[/green] {dst.name}")
            ok += 1
        except StudioError as e:
            console.print(f"  [red]error[/red] {src.name}: {e}")
    console.print(f"[bold green]{ok}/{len(targets)} procesados.[/bold green] Los originales no se tocaron.")


@app.command()
def gain(
    files: Annotated[list[Path], typer.Argument(help="Archivos de audio o una carpeta")],
    album: Annotated[bool, typer.Option("--album/--track", help="Ganancia de álbum (conjunto) o por pista")] = False,
) -> None:
    """Escribe tags ReplayGain 2.0 (EBU R128). No destructivo: el audio no se toca."""
    from hifihub.audio.loudness import LoudnessError, scan_files
    from hifihub.library.scanner import iter_audio_files

    targets: list[Path] = []
    for f in files:
        targets.extend(iter_audio_files(f) if f.is_dir() else [f])
    if not targets:
        console.print("[yellow]No hay archivos de audio.[/yellow]")
        raise typer.Exit(1)
    try:
        results = scan_files(targets, album=album)
    except LoudnessError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    table = Table(title=f"ReplayGain ({'álbum' if album else 'pista'})")
    table.add_column("Archivo")
    table.add_column("LUFS", justify="right")
    table.add_column("Ganancia", justify="right")
    for r in results:
        table.add_row(Path(r.path).name,
                      f"{r.loudness_lufs:.1f}" if r.loudness_lufs is not None else "?",
                      f"{r.gain_db:+.2f} dB" if r.gain_db is not None else "?")
    console.print(table)

# Normalizar, evita que el volumen varie demasiado entre tracks de un album, se usa en general pero siempre dejo la opcion de desactivarlo.
@app.command()
def loudnorm(
    file: Annotated[Path, typer.Argument(help="Archivo a normalizar (SE REESCRIBE)")],
    lufs: Annotated[float, typer.Option("--lufs", help="Loudness objetivo")] = -14.0,
) -> None:
    """Quema loudnorm EBU R128 en el archivo. DESTRUCTIVO: recodifica el audio.

    Para volumen uniforme sin pérdida usa 'gain' (tags ReplayGain)."""
    from hifihub.audio.loudness import LoudnessError, loudnorm_two_pass

    console.print("[yellow]Aviso:[/yellow] loudnorm recodifica el archivo (pérdida en fuentes lossy). "
                  "La alternativa no destructiva es [bold]hifihub gain[/bold].")
    try:
        measured = loudnorm_two_pass(file, target_lufs=lufs)
    except LoudnessError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]OK[/green] {file.name}: {measured['input_i']} LUFS -> objetivo {lufs} LUFS")

# Esto es codigo generico para actualizar ytdlp. evita tener que cambiar la version y volver a recompilar.
@app.command()
def update(
    check_only: Annotated[bool, typer.Option("--check", help="Solo comprobar, sin instalar")] = False,
) -> None:
    """Actualiza el motor de descargas (yt-dlp) a la última versión."""
    from hifihub.engine.updater import UpdateError, apply_update, check

    st = check()
    if not st["ok"]:
        console.print(f"[red]No se pudo consultar PyPI:[/red] {st.get('error')}")
        raise typer.Exit(1)
    console.print(f"Motor yt-dlp: [bold]{st['current']}[/bold] ({st['source']}) · última: [bold]{st['latest']}[/bold]")
    if not st["update_available"]:
        console.print("[green]Ya está al día.[/green]")
        return
    if check_only:
        console.print(f"[yellow]Actualización disponible: {st['latest']}[/yellow] (ejecuta 'hifihub update' para instalar)")
        return
    try:
        result = apply_update()
    except UpdateError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[bold green]{result['message']}[/bold green]")


session_app = typer.Typer(help="Sesión de YouTube: importación única, filtrada y cifrada (DPAPI).")
app.add_typer(session_app, name="session")


@session_app.command("status")
def session_status() -> None:
    """Estado de la sesión de YouTube."""
    from hifihub.engine import session

    st = session.status()
    if not st["connected"]:
        console.print("[yellow]Sin sesión.[/yellow] Conecta con: hifihub session connect firefox")
        return
    console.print(f"[green]Sesión conectada[/green] — origen: {st.get('source')}, "
                  f"{st.get('cookies')} cookies, importada: {st.get('imported_at')}")

# El tema de sesiones termino siendo descartado, aunque sirve no elimina la limitante de cantidad de descargas, se pensaba para los users de youtube premium pero realmente no genera mayor diferencia y la mejora de calidad tampoco es tan significativa.
@session_app.command("connect")
def session_connect(
    browser: Annotated[str, typer.Argument(help="firefox | chrome | edge | brave")] = "firefox",
) -> None:
    """Importa la sesión desde el navegador (una sola vez, filtrada y cifrada)."""
    from hifihub.engine.session import SessionError, connect_from_browser

    if browser != "firefox":
        console.print(f"[yellow]Cierra {browser} por completo antes de continuar.[/yellow]")
    try:
        meta = connect_from_browser(browser)
    except SessionError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Sesión conectada:[/green] {meta['cookies']} cookies de YouTube/Google, "
                  "guardadas cifradas. La app no volverá a tocar el navegador.")

# Misma historia, era para poder darle la cookie al archivo pero se descarto, igual la app nunca lo almacenaba solo lo leia, iniciaba la sesion guardando el codigo filtrado y te pedia que eliminaras el archivo.
@session_app.command("connect-file")
def session_connect_file(
    file: Annotated[Path, typer.Argument(help="cookies.txt exportado")],
) -> None:
    """Importa un cookies.txt, lo filtra y lo guarda cifrado."""
    from hifihub.engine.session import SessionError, connect_from_file

    try:
        meta = connect_from_file(file)
    except SessionError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Sesión importada:[/green] {meta['cookies']} cookies. "
                  "Ya puedes borrar el cookies.txt original.")

# Igual manera permitia cerrar la sesion y eliminaba el registro cifrado. Nuevamente esto ya no se usa pero se deja por si a alguien le interesa hacer un workaround.
@session_app.command("disconnect")
def session_disconnect() -> None:
    """Borra la sesión guardada."""
    from hifihub.engine import session

    session.disconnect()
    console.print("[green]Sesión cerrada y borrada.[/green]")

# Todos estos son los valores que se pueden ajustar en la config.
@app.command("config")
def show_config() -> None:
    """Muestra la configuración actual y dónde vive."""
    from hifihub import paths

    cfg = Config.load()
    table = Table(title=f"HiFi Hub v{__version__} — {paths.CONFIG_FILE}")
    table.add_column("Clave")
    table.add_column("Valor")
    table.add_row("download_dir", cfg.download_dir)
    table.add_row("default_profile", cfg.default_profile)
    table.add_row("cookies_browser", cfg.cookies_browser or "(desactivado)")
    table.add_row("multi_source", "sí" if cfg.multi_source else "no")
    table.add_row("batch_concurrency", str(cfg.batch_concurrency))
    table.add_row("enrich_library", "sí" if cfg.enrich_library else "no")
    table.add_row("acoustid_api_key", "(configurada)" if cfg.acoustid_api_key else "(no configurada)")
    table.add_row("apply_replaygain", "sí" if cfg.apply_replaygain else "no")
    table.add_row("replaygain (reproducción)", f"{cfg.replaygain_mode} · preamp {cfg.replaygain_preamp:+g} dB")
    table.add_row("wasapi_exclusive", "sí" if cfg.wasapi_exclusive else "no")
    table.add_row("headphone_correction", cfg.headphone_correction or "(ninguna)")
    table.add_row("crossfeed", f"{cfg.crossfeed:g}" if cfg.crossfeed else "off")
    table.add_row("sponsorblock", "sí" if cfg.sponsorblock else "no")
    table.add_row("analyze_bpm_key", "sí" if cfg.analyze_bpm_key else "no")
    table.add_row("lastfm", "configurado" if cfg.lastfm_session_key else "no")
    table.add_row("herramientas", str(paths.tools_dir()))
    console.print(table)


@app.command("config-init")
def init_config() -> None:
    """Crea el archivo de configuración con los valores por defecto."""
    file = Config.load().save()
    console.print(f"[green]Configuración guardada en[/green] {file}")


if __name__ == "__main__":
    app()
