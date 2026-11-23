"use strict";

// ============================================================================
// BIBLIOTECA
// Una sola barra (pestañas + búsqueda + orden + lossless + escanear), listas
// paginadas y páginas de detalle (álbum / artista / playlist) con HISTORIAL:
// «Volver a…» deshace el camino, y si se llegó desde otra vista (Para ti,
// Reproduciendo…) devuelve allí.
//
// Usa utilidades de app.js ($, escapeHtml, fmtDur, artistLink, lazyCover,
// releaseCovers, markPlaying, confirmDialog, promptDialog, toast,
// openContextMenu, openPlaylistMenuForMany, placeMenu, closeMenus, switchView).
// ============================================================================

let CURRENT_SUB = "albums";   // pestaña activa (app.js la consulta)

const LIB = {
  tab: "albums",
  q: "",
  sort: { albums: "artist", artists: "name", songs: "artist" },
  ll: false,
  page: { albums: 1, artists: 1, songs: 1 },
  per: { albums: 24, artists: 24, songs: 15 },
  data: {},          // listas ya pedidas; se invalidan al escanear/editar/borrar
  stack: [],         // páginas de detalle abiertas: [{type, id, title}]
  origin: null,      // vista desde la que se abrió el detalle (si no era Biblioteca)
  listScroll: 0,
  newOpen: false,
  ctx: null,         // contexto de reproducción de la tabla visible
  model: null,       // datos de la página de detalle visible
  tok: 0,            // descarta renders viejos si llega uno más nuevo
};

const LIB_TABS = { albums: "Álbumes", artists: "Artistas", songs: "Canciones", playlists: "Playlists" };
const LIB_PH = {
  albums: "Buscar álbumes o artistas",
  artists: "Buscar artistas",
  songs: "Buscar canciones, artistas o álbumes",
  playlists: "Buscar playlists",
};
const LIB_SORTS = {
  albums: [["artist", "Artista"], ["title", "Título"], ["year", "Año"], ["added", "Recién añadido"],
           ["plays", "Más escuchado"], ["tracks", "Más pistas"], ["duration", "Duración"]],
  artists: [["name", "Nombre"], ["tracks", "Más pistas"], ["albums", "Más álbumes"]],
  songs: [["artist", "Artista"], ["title", "Título"], ["album", "Álbum"], ["year", "Año"],
          ["added", "Recién añadido"], ["plays", "Más escuchado"], ["duration", "Duración"]],
};
// Rejillas: múltiplos que llenan filas de 6, 4 o 3 columnas.
const LIB_PER = { albums: [12, 24, 48], artists: [12, 24, 48], songs: [15, 30, 50] };
const VIEW_LABELS = { parati: "Para ti", reproduciendo: "Reproduciendo", descargas: "Descargas",
                      importar: "Importar", estudio: "Estudio", ajustes: "Ajustes" };
const LIB_REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Búsqueda sin tildes ni mayúsculas: «musica» encuentra «Música».
const libNorm = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const libMatch = (...fields) => !LIB.q || fields.some((f) => libNorm(f).includes(libNorm(LIB.q)));
const libCmp = (a, b) => String(a || "").localeCompare(String(b || ""), "es", { sensitivity: "base", numeric: true });
const libNum = (v) => (typeof v === "number" ? v : parseFloat(v) || 0);
const noThe = (s) => String(s || "").replace(/^the /i, "");
const closest = (v, opts) => opts.reduce((b, o) => (Math.abs(o - v) < Math.abs(b - v) ? o : b), opts[0]);
const libActive = () => $("#view-biblioteca").classList.contains("active");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// --- Arranque -------------------------------------------------------------------

function wireLibrary(cfg) {
  LIB.per.songs = closest(cfg.songs_per_page || 15, LIB_PER.songs);
  LIB.per.albums = LIB.per.artists = closest(cfg.grid_per_page || 24, LIB_PER.albums);

  const seg = $("#lib-seg");
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) setTab(b.dataset.tab);
  });
  seg.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    e.stopPropagation();   // las flechas globales moverían la canción ±5 s
    const tabs = [...seg.querySelectorAll("[data-tab]")];
    const i = tabs.findIndex((b) => b.dataset.tab === LIB.tab);
    const n = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    setTab(n.dataset.tab);
    n.focus();
  });

  let qt;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(qt);
    qt = setTimeout(() => {
      LIB.q = e.target.value.trim();
      LIB.stack = [];
      LIB.origin = null;
      LIB.page[LIB.tab] = 1;
      renderLib(false);
    }, 120);
  });
  $("#lib-sort").addEventListener("change", (e) => {
    LIB.sort[LIB.tab] = e.target.value;
    LIB.page[LIB.tab] = 1;
    LIB.stack = [];
    renderLib();
  });
  $("#lib-lossless").addEventListener("click", () => {
    LIB.ll = !LIB.ll;
    $("#lib-lossless").setAttribute("aria-pressed", String(LIB.ll));
    LIB.page.albums = LIB.page.songs = 1;
    LIB.stack = [];
    renderLib(false);
  });
  $("#btn-scan").addEventListener("click", () => rescanLibrary(true));

  const view = $("#lib-view");
  view.addEventListener("click", onLibClick);
  view.addEventListener("dblclick", (e) => {
    const row = e.target.closest(".lb-tr[data-i]");
    if (row && !e.target.closest("button, .artist-link")) playFromRow(+row.dataset.i);
  });
  // Clic derecho en una canción = el mismo menú «⋯».
  view.addEventListener("contextmenu", (e) => {
    const row = e.target.closest(".lb-tr[data-track-id]");
    if (!row) return;
    e.preventDefault();
    const x = e.clientX, y = e.clientY;
    openContextMenu({ getBoundingClientRect: () => ({ left: x, right: x, top: y, bottom: y, width: 0, height: 0 }) },
      parseInt(row.dataset.trackId));
  });
  view.addEventListener("change", async (e) => {
    if (!e.target.matches("[data-per]")) return;
    const v = parseInt(e.target.value);
    if (LIB.tab === "songs") {
      LIB.per.songs = v;
      await window.pywebview.api.save_config({ songs_per_page: v });
    } else {
      LIB.per.albums = LIB.per.artists = v;
      await window.pywebview.api.save_config({ grid_per_page: v });
    }
    LIB.page[LIB.tab] = 1;
    renderLib(false);
  });
  view.addEventListener("submit", async (e) => {
    if (e.target.id !== "lb-new-form") return;
    e.preventDefault();
    const name = $("#lb-new-name").value.trim();
    if (!name) return;
    const res = await window.pywebview.api.playlist_create(name);
    if (!res.ok) return toast(res.error, true);
    LIB.newOpen = false;
    LIB.data.playlists = null;
    renderLib(false);
  });

  // Nombres de artista clicables en CUALQUIER vista (delegación global).
  document.addEventListener("click", (e) => {
    const link = e.target.closest(".artist-link");
    if (!link) return;
    e.stopPropagation();
    openArtist(link.dataset.artist === "" ? null : link.dataset.artist);
  });

  // Esc: primero cierra menús/modales (otros manejadores); si no hay nada
  // abierto y estamos en un detalle, vuelve atrás. En CAPTURA para decidir
  // antes de que el manejador global cierre el menú.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !libActive() || !LIB.stack.length) return;
    const busy = [...document.querySelectorAll(".modal")].some((m) => !m.hidden) ||
      !$("#ctxmenu").hidden || !$("#plmenu").hidden;
    if (busy || isTyping(e)) return;
    libBack();
  }, true);

  // La barra es fija bajo la cabecera, que cambia de alto con el ancho.
  const hdr = document.querySelector("header");
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--hdr-h", hdr.offsetHeight + "px");
  }).observe(hdr);
  new ResizeObserver(() => { moveInd(); fitLibTitle(); }).observe(seg);

  syncLibBar();
}

// --- Pestañas y barra -----------------------------------------------------------

function setTab(tab) {
  LIB.tab = CURRENT_SUB = tab;
  LIB.stack = [];
  LIB.origin = null;
  LIB.q = "";
  LIB.newOpen = false;
  $("#search").value = "";
  syncLibBar();
  renderLib();
  window.scrollTo({ top: 0, behavior: "instant" });
}

// Compatibilidad con llamadas antiguas.
function switchSub(name) {
  setTab(name);
}

function syncLibBar() {
  const t = LIB.tab;
  $("#lib-seg").querySelectorAll("[data-tab]").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.tab === t))
  );
  moveInd();
  $("#search").placeholder = LIB_PH[t];
  const so = LIB_SORTS[t];
  $("#lib-sort-wrap").hidden = !so;
  if (so) {
    $("#lib-sort").innerHTML = so
      .map(([v, l]) => `<option value="${v}"${LIB.sort[t] === v ? " selected" : ""}>Orden: ${l}</option>`)
      .join("");
  }
  $("#lib-lossless").hidden = !(t === "albums" || t === "songs");
}

// Indicador inclinado que se desliza bajo la pestaña activa.
function moveInd() {
  const b = $("#lib-seg").querySelector('[aria-selected="true"]');
  if (!b || !b.offsetWidth) return;
  const ind = $("#lib-ind");
  ind.style.left = b.offsetLeft + "px";
  ind.style.width = b.offsetWidth + "px";
}

function libReplay() {
  const v = $("#lib-view");
  v.classList.remove("enter");
  void v.offsetWidth;
  v.classList.add("enter");
}

// --- Datos ------------------------------------------------------------------------

async function libData(kind) {
  if (LIB.data[kind]) return LIB.data[kind];
  const api = window.pywebview.api;
  if (kind === "albums") LIB.data.albums = await api.list_albums();
  else if (kind === "artists") LIB.data.artists = await api.list_artists();
  else if (kind === "songs") LIB.data.songs = await api.all_tracks();
  else if (kind === "stats") LIB.data.stats = await api.library_stats();
  else if (kind === "playlists") LIB.data.playlists = await playlistsMeta();
  else if (kind === "smart") LIB.data.smart = await api.smart_list();
  return LIB.data[kind];
}

// Mosaico y duración de cada playlist (se calculan aquí; el backend solo da id/nombre).
async function playlistsMeta() {
  const api = window.pywebview.api;
  const lists = await api.playlists_list();
  return Promise.all(lists.map(async (p) => {
    const tracks = await api.playlist_tracks(p.id);
    const covers = [...new Set(tracks.map((t) => t.cover_path).filter(Boolean))].slice(0, 4);
    return { ...p, n: tracks.length, covers,
             duration: tracks.reduce((s, t) => s + (t.duration || 0), 0) };
  }));
}

// Tras escanear, editar o borrar: todo se vuelve a pedir y se repinta lo visible.
function refreshLibrary() {
  LIB.data = {};
  if (LIB.stack.length) return renderDetail(false, null, true);
  return renderLib(false);
}

function loadPlaylists() {
  LIB.data.playlists = null;
  if (LIB.tab === "playlists" && !LIB.stack.length) renderLib(false);
}

async function rescanLibrary(fromButton = false) {
  const btn = $("#btn-scan");
  btn.classList.add("spin");
  btn.querySelector("span").textContent = "Escaneando";
  try {
    const res = await window.pywebview.api.scan_library();
    clearCoverCache();
    if (fromButton) toast(`Biblioteca al día: ${res.tracks} pistas · ${res.albums} álbumes · ${res.artists} artistas`);
  } finally {
    btn.classList.remove("spin");
    btn.querySelector("span").textContent = "Escanear";
  }
  return refreshLibrary();
}

// --- Orden ----------------------------------------------------------------------

function sortAlbumList(list, mode) {
  const by = {
    artist: (a, b) => libCmp(noThe(a.album_artist), noThe(b.album_artist)) || libCmp(a.album, b.album),
    title: (a, b) => libCmp(a.album, b.album),
    year: (a, b) => libCmp(b.date, a.date),
    added: (a, b) => libNum(b.added_at) - libNum(a.added_at),
    plays: (a, b) => libNum(b.play_count) - libNum(a.play_count),
    tracks: (a, b) => libNum(b.track_count) - libNum(a.track_count),
    duration: (a, b) => libNum(b.total_duration) - libNum(a.total_duration),
  };
  return list.slice().sort(by[mode] || by.artist);
}

function sortArtistList(list, mode) {
  const by = {
    name: (a, b) => libCmp(noThe(a.name), noThe(b.name)),
    tracks: (a, b) => libNum(b.track_count) - libNum(a.track_count),
    albums: (a, b) => libNum(b.album_count) - libNum(a.album_count),
  };
  return list.slice().sort(by[mode] || by.name);
}

function sortTrackList(list, mode) {
  const tn = (a, b) => libNum(a.track_number) - libNum(b.track_number);
  const by = {
    artist: (a, b) => libCmp(noThe(a.album_artist || a.artist), noThe(b.album_artist || b.artist)) ||
                      libCmp(a.album, b.album) || tn(a, b),
    title: (a, b) => libCmp(a.title, b.title),
    album: (a, b) => libCmp(a.album, b.album) || tn(a, b),
    year: (a, b) => libCmp(b.date, a.date),
    added: (a, b) => libNum(b.added_at) - libNum(a.added_at),
    plays: (a, b) => libNum(b.play_count) - libNum(a.play_count),
    duration: (a, b) => libNum(b.duration) - libNum(a.duration),
  };
  return list.slice().sort(by[mode] || by.artist);
}

// --- Listas -----------------------------------------------------------------------

async function renderLib(animate = true) {
  if (LIB.stack.length) return renderDetail(animate);
  const tok = ++LIB.tok;
  const t = LIB.tab;
  const stats = await libData("stats");
  let html = "";

  if (t === "albums") {
    const list = sortAlbumList((await libData("albums"))
      .filter((a) => (!LIB.ll || a.all_lossless) && libMatch(a.album, a.album_artist)), LIB.sort.albums);
    const pg = libPage(list, "álbumes");
    html = list.length ? `<div class="lb-agrid">${pg.items.map(albumCard).join("")}</div>${pg.pager}` : "";
  } else if (t === "artists") {
    const list = sortArtistList((await libData("artists")).filter((a) => libMatch(a.name)), LIB.sort.artists);
    const pg = libPage(list, "artistas");
    html = list.length ? `<div class="lb-argrid">${pg.items.map(artistCard).join("")}</div>${pg.pager}` : "";
  } else if (t === "songs") {
    const list = sortTrackList((await libData("songs"))
      .filter((s) => (!LIB.ll || s.lossless) && libMatch(s.title, s.artist, s.album_artist, s.album)), LIB.sort.songs);
    const pg = libPage(list, "canciones");
    LIB.ctx = { kind: "ids", ids: list.map((s) => s.id) };
    html = list.length ? trackTable(pg.items, "songs", pg.offset) + pg.pager : "";
  } else {
    const list = (await libData("playlists")).filter((p) => libMatch(p.name));
    const smart = (await libData("smart")).filter((p) => libMatch(p.name));   // smart.js
    html = `<div class="lb-pgrid">${LIB.q ? "" : newPlaylistCard()}${smart.map(smartCard).join("")}${list.map(playlistCard).join("")}</div>`;
    if (!list.length && !smart.length && LIB.q) html = "";
  }
  if (tok !== LIB.tok) return;

  if (!html) html = libEmpty(stats, t);
  const view = $("#lib-view");
  releaseCovers(view);
  view.innerHTML = html;
  view.querySelectorAll("[data-cover]").forEach((el) => lazyCover(el, el.dataset.cover));
  if (LIB.newOpen) { const n = $("#lb-new-name"); if (n) n.focus(); }
  markPlaying();
  if (animate) libReplay();
}

function libEmpty(stats, tab) {
  if (!stats.tracks && tab !== "playlists") {
    return `<div class="empty-state">
      <i class="ph-bold ph-music-notes-plus"></i>
      <h4>Tu biblioteca está vacía</h4>
      <p>Importa música que ya tengas o descarga algo para empezar.</p>
      <div class="actions">
        <button class="primary" data-go="importar"><i class="ph-bold ph-folder-plus"></i>Importar música</button>
        <button class="secondary" data-go="descargas"><i class="ph-bold ph-download-simple"></i>Descargar</button>
      </div></div>`;
  }
  if (LIB.q) return `<div class="lb-empty">No hay resultados para «${escapeHtml(LIB.q)}».</div>`;
  if (LIB.ll) return `<div class="lb-empty">Nada completamente lossless aquí. Desactiva «Solo lossless».</div>`;
  return `<div class="lb-empty">Aún no hay nada aquí.</div>`;
}

// Paginación: corta la lista y devuelve la barra de páginas (con el total).
function libPage(list, noun) {
  const t = LIB.tab, per = LIB.per[t];
  const pages = Math.max(1, Math.ceil(list.length / per));
  LIB.page[t] = Math.min(Math.max(1, LIB.page[t]), pages);
  const p = LIB.page[t];
  return { items: list.slice((p - 1) * per, p * per), offset: (p - 1) * per,
           pager: pagerHTML(p, pages, list.length, per, noun) };
}

function pagerHTML(p, pages, total, per, noun) {
  const nums = [];
  for (let i = 1; i <= pages; i++) {
    if (i === 1 || i === pages || Math.abs(i - p) <= 1) nums.push(i);
    else if (nums[nums.length - 1] !== "…") nums.push("…");
  }
  const from = (p - 1) * per + 1, to = Math.min(total, p * per);
  const opts = LIB_PER[LIB.tab].map((n) => `<option value="${n}"${n === per ? " selected" : ""}>${n} por página</option>`).join("");
  return `<nav class="lb-pager" aria-label="Páginas">
      <button class="lb-pg-nav" data-page="${p - 1}"${p <= 1 ? " disabled" : ""} aria-label="Página anterior"><i class="ph-bold ph-caret-left"></i></button>
      <span class="lb-pg-nums">${nums.map((n) => n === "…"
        ? '<span class="lb-pg-gap">…</span>'
        : `<button class="lb-pg-n" data-page="${n}"${n === p ? ' aria-current="page"' : ""}>${n}</button>`).join("")}</span>
      <button class="lb-pg-nav" data-page="${p + 1}"${p >= pages ? " disabled" : ""} aria-label="Página siguiente"><i class="ph-bold ph-caret-right"></i></button>
      <span class="lb-pg-range">${from}–${to} de ${total} ${noun}</span>
      <span class="lb-sel lb-pg-per"><select data-per aria-label="Por página">${opts}</select></span>
    </nav>`;
}

function albumCard(a) {
  const name = a.album || "(sin álbum)";
  return `<div class="lb-alb">
      <button class="lb-alb-main" data-open-album="${escapeHtml(a.album || "")}">
        <span class="lb-sl">
          <span class="fy-vin"><span class="fy-vin-label" data-cover="${escapeHtml(a.cover_path || "")}"></span></span>
          <span class="fy-sleeve lb-src" data-cover="${escapeHtml(a.cover_path || "")}"></span>
        </span>
        <span class="lb-alb-t"><b>${escapeHtml(name)}</b>${a.all_lossless ? '<span class="lb-q">LOSSLESS</span>' : ""}</span>
      </button>
      ${artistLink(a.album_artist)}
    </div>`;
}

function artistCard(a) {
  return `<button class="fy-artist lb-artist" data-open-artist="${escapeHtml(a.name || "")}">
      <span class="fy-plate"></span>
      <span class="fy-portrait lb-src" data-cover="${escapeHtml(a.cover_path || "")}"></span>
      <b>${escapeHtml(a.name || "(sin artista)")}</b>
      <small>${plural(a.album_count, "álbum", "álbumes")} · ${plural(a.track_count, "pista", "pistas")}</small>
    </button>`;
}

function mosaicHTML(covers, cls = "") {
  if (covers.length >= 4) {
    return `<span class="lb-mosaic four ${cls}">${covers.slice(0, 4).map((c) => `<i data-cover="${escapeHtml(c)}"></i>`).join("")}</span>`;
  }
  if (covers.length) return `<span class="lb-mosaic ${cls}"><i data-cover="${escapeHtml(covers[0])}"></i></span>`;
  return `<span class="lb-mosaic empty ${cls}"></span>`;
}

function playlistCard(p) {
  return `<button class="lb-pl" data-open-playlist="${p.id}">
      <span class="lb-mo">${mosaicHTML(p.covers, "lb-src")}<span class="lb-band"><span>${escapeHtml(p.name)}</span></span></span>
      <small>${plural(p.n, "pista", "pistas")}${p.duration ? " · " + fmtDur(p.duration) : ""}</small>
    </button>`;
}

function newPlaylistCard() {
  if (LIB.newOpen) {
    return `<div class="lb-new"><div class="lb-new-box open">
        <form id="lb-new-form"><input type="text" id="lb-new-name" placeholder="Nombre de la playlist" aria-label="Nombre de la playlist">
        <button class="lb-btn pri" type="submit"><i class="ph-bold ph-plus"></i>Crear</button>
        <button class="lb-btn" type="button" data-act="new-cancel">Cancelar</button></form>
      </div></div>`;
  }
  return `<div class="lb-new">
      <button class="lb-new-box" data-act="new-open"><i class="ph-bold ph-plus"></i><b>Nueva playlist</b></button>
      <button class="lb-imp" data-act="import-m3u">Importar M3U8</button>
      <button class="lb-imp lb-imp-smart" data-act="smart-new"><i class="ph-bold ph-magic-wand"></i>Nueva inteligente</button>
    </div>`;
}

// --- Tabla de canciones -------------------------------------------------------------
// Un solo componente para Canciones, álbum, artista y playlist. data-i es la
// posición dentro del CONTEXTO de reproducción (la lista entera, no la página).

function trackTable(list, mode, offset = 0) {
  const cols = {
    songs: ["Título", "Artista", "Álbum"],
    album: ["Título"],
    artist: ["Título", "Álbum"],
    playlist: ["Título", "Artista", "Álbum"],
  }[mode];
  const head = `<div class="lb-tr head"><span class="lb-ix">#</span>${cols
    .map((c, k) => `<span class="${k === 1 ? (mode === "artist" ? "lb-al" : "lb-ar") : k === 2 ? "lb-al" : ""}">${c}</span>`)
    .join("")}<span class="lb-du">Dur.</span><span></span></div>`;
  return `<div class="lb-tbl m-${mode}" role="table">${head}${list.map((t, k) => trackRow(t, offset + k, mode, k)).join("")}</div>`;
}

function trackRow(t, i, mode, k) {
  const num = mode === "album" ? t.track_number || i + 1 : i + 1;
  const who = t.artist || t.album_artist || "";
  const thumb = mode === "album" ? "" : `<span class="lb-thumb" data-cover="${escapeHtml(t.cover_path || "")}"></span>`;
  const artistCell = `<span class="lb-ar">${artistLink(who)}</span>`;
  const albumCell = `<span class="lb-al">${t.album
    ? `<button class="lb-lnk" data-open-album="${escapeHtml(t.album)}">${escapeHtml(t.album)}</button>`
    : '<span class="lb-none">—</span>'}</span>`;
  const cells = mode === "album" ? "" : mode === "artist" ? albumCell : artistCell + albumCell;
  const acts = mode === "playlist"
    ? `<button class="row-mini" data-move="-1" data-pos="${k}" title="Subir"><i class="ph-bold ph-caret-up"></i></button>
       <button class="row-mini" data-move="1" data-pos="${k}" title="Bajar"><i class="ph-bold ph-caret-down"></i></button>
       <button class="row-mini" data-remove="${t.id}" title="Quitar de la playlist"><i class="ph-bold ph-x"></i></button>
       <button class="row-ctx" data-id="${t.id}" title="Más acciones"><i class="ph-bold ph-dots-three"></i></button>`
    : `<button class="row-add" data-id="${t.id}" title="Añadir a playlist"><i class="ph-bold ph-plus"></i></button>
       <button class="row-ctx" data-id="${t.id}" title="Más acciones"><i class="ph-bold ph-dots-three"></i></button>`;
  return `<div class="lb-tr" data-i="${i}" data-track-id="${t.id}">
      <span class="lb-ix"><span class="track-num">${num}</span><button class="lb-pb" data-play="${i}" title="Reproducir"><i class="ph-bold ph-play"></i></button></span>
      <span class="lb-tt">${thumb}<span class="lb-tx"><b>${escapeHtml(t.title || "")}</b><small>${escapeHtml(who)}</small></span></span>
      ${cells}
      <span class="lb-du">${fmtDur(t.duration)}</span>
      <span class="lb-acts">${acts}</span>
    </div>`;
}

function playFromRow(i) {
  const api = window.pywebview.api, c = LIB.ctx;
  if (!c) return;
  if (c.kind === "ids") api.play_ids(c.ids, i);
  else if (c.kind === "album") api.play_album_from(c.album, i);
  else if (c.kind === "artist") api.play_artist_from(c.name, i);
  else if (c.kind === "playlist") api.play_playlist(c.pid, i);
}

// --- Clics dentro de la vista ---------------------------------------------------------

async function onLibClick(e) {
  const t = e.target;
  if (t.closest(".artist-link")) return;           // lo resuelve la delegación global
  const go = t.closest("[data-go]");
  if (go) return switchView(go.dataset.go);
  if (t.closest("[data-back]")) return libBack();

  const play = t.closest("[data-play]");
  if (play) return playFromRow(+play.dataset.play);
  const alb = t.closest("[data-open-album]");
  if (alb) return openAlbum(alb.dataset.openAlbum || null, alb);
  const art = t.closest("[data-open-artist]");
  if (art) return openArtist(art.dataset.openArtist || null, art);
  const pl = t.closest("[data-open-playlist]");
  if (pl) return openPlaylist(+pl.dataset.openPlaylist, pl);

  const pg = t.closest("[data-page]");
  if (pg && !pg.disabled) {
    LIB.page[LIB.tab] = +pg.dataset.page;
    await renderLib(false);
    window.scrollTo({ top: 0, behavior: "instant" });
    return;
  }
  const mv = t.closest("[data-move]");
  if (mv) return movePlaylistTrack(+mv.dataset.pos, +mv.dataset.move);
  const rm = t.closest("[data-remove]");
  if (rm) return removePlaylistTrack(+rm.dataset.remove);

  const act = t.closest("[data-act]");
  if (act) return libAction(act.dataset.act, act);
}

async function libAction(act, el) {
  const api = window.pywebview.api;
  const m = LIB.model;
  switch (act) {
    case "new-open": LIB.newOpen = true; return renderLib(false);
    case "new-cancel": LIB.newOpen = false; return renderLib(false);
    case "import-m3u": {
      const res = await api.playlist_import();
      if (!res.ok) return res.error !== "Cancelado." && toast(res.error, true);
      toast(`Importada (${res.added} pistas). ${res.note}`);
      return loadPlaylists();
    }
    case "play":
      if (m.type === "album") api.play_album(m.album);
      else if (m.type === "artist") api.play_artist(m.name, false);
      else api.play_playlist(m.pid, 0);
      return;
    case "shuffle":
      if (m.type === "artist") return api.play_artist(m.name, true);
      return api.play_ids(shuffled(m.tracks.map((x) => x.id)), 0);
    case "addpl":
      return openPlaylistMenuForMany(el, m.tracks.map((x) => x.id), m.title);
    case "export": {
      const res = await api.playlist_export(m.pid);
      if (res.ok) toast("Exportada: " + res.path);
      else if (res.error !== "Cancelado.") toast(res.error, true);
      return;
    }
    case "more":
      return openActionMenu(el, m.type === "album"
        // «(sin álbum)» mezcla discos distintos: ahí no tiene sentido una portada común.
        ? [...(m.album ? [["Cambiar portada…", () => changeCover(m.tracks.map((x) => x.id))]] : []),
           ["Eliminar álbum…", deleteAlbum, true]]
        : [["Renombrar…", renamePlaylist], ["Eliminar playlist…", deletePlaylist, true]]);
  }
}

// Menú «⋯» de la cabecera (reutiliza el menú flotante de la app).
function openActionMenu(anchor, items) {
  const menu = $("#ctxmenu");
  menu.innerHTML = "";
  items.forEach(([label, fn, danger]) => {
    const it = document.createElement("div");
    it.className = "plmenu-item" + (danger ? " danger" : "");
    it.textContent = label;
    it.addEventListener("click", () => { menu.hidden = true; fn(); });
    menu.appendChild(it);
  });
  menu.hidden = false;
  placeMenu(menu, anchor);
}

async function deleteAlbum() {
  const m = LIB.model;
  const ok = await confirmDialog({
    kick: "Eliminar del disco",
    title: "Eliminar álbum",
    msg: `Se enviarán a la Papelera de reciclaje las ${plural(m.tracks.length, "pista", "pistas")} de «${m.title}» y se quitarán de la biblioteca.`,
  });
  if (!ok) return;
  const res = await window.pywebview.api.delete_album(m.album);
  toast(res.ok ? `Álbum eliminado (${res.count} pista(s) a la Papelera).` : "No se pudo eliminar.", !res.ok);
  LIB.data = {};
  libBack();
}

async function renamePlaylist() {
  const m = LIB.model;
  const name = await promptDialog({ kick: "Playlist", title: "Renombrar playlist", value: m.title });
  if (!name) return;
  const res = await window.pywebview.api.playlist_rename(m.pid, name);
  if (!res.ok) return toast(res.error, true);
  LIB.data.playlists = null;
  renderDetail(false, null, true);
}

async function deletePlaylist() {
  const m = LIB.model;
  const ok = await confirmDialog({
    kick: "Eliminar playlist",
    title: "Eliminar playlist",
    msg: `Se eliminará «${m.title}». Las canciones siguen en tu biblioteca.`,
  });
  if (!ok) return;
  await window.pywebview.api.playlist_delete(m.pid);
  toast("Playlist eliminada (las canciones siguen en la biblioteca).");
  LIB.data.playlists = null;
  libBack();
}

async function movePlaylistTrack(pos, delta) {
  await window.pywebview.api.playlist_move(LIB.model.pid, pos, delta);
  LIB.data.playlists = null;
  renderDetail(false, null, true);
}

async function removePlaylistTrack(trackId) {
  await window.pywebview.api.playlist_remove(LIB.model.pid, trackId);
  LIB.data.playlists = null;
  renderDetail(false, null, true);
}

// --- Detalle e historial --------------------------------------------------------------

function openAlbum(album, src = null) {
  return libOpen("album", album === "" ? null : album, src);
}

function openArtist(name, src = null) {
  return libOpen("artist", name === "" ? null : name, src);
}

function openPlaylist(pid, src = null) {
  return libOpen("playlist", pid, src);
}

function libOpen(type, id, src) {
  closeMenus();
  if (!libActive()) {
    // Desde otra vista: «Volver» devolverá allí.
    const prev = document.querySelector(".view.active");
    LIB.origin = prev ? prev.id.replace("view-", "") : null;
    LIB.stack = [];
    switchView("biblioteca");
  } else if (!LIB.stack.length) {
    LIB.listScroll = window.scrollY;
    LIB.origin = null;
  }
  LIB.stack.push({ type, id, title: "" });
  return renderDetail(true, src);
}

function libBack() {
  if (LIB.stack.length > 1) {
    LIB.stack.pop();
    return renderDetail(true);
  }
  LIB.stack = [];
  if (LIB.origin) {
    const o = LIB.origin;
    LIB.origin = null;
    renderLib(false);
    return switchView(o);
  }
  return renderLib(true).then(() => window.scrollTo({ top: LIB.listScroll || 0, behavior: "instant" }));
}

function backLabel() {
  const prev = LIB.stack[LIB.stack.length - 2];
  if (prev) return prev.title || "atrás";
  if (LIB.origin) return VIEW_LABELS[LIB.origin] || "atrás";
  return LIB_TABS[LIB.tab];
}

async function detailModel(top) {
  const api = window.pywebview.api;
  if (top.type === "smart") return smartModel(top);   // smart.js
  if (top.type === "album") {
    const tracks = await api.album_tracks(top.id);
    const artist = tracks.map((t) => t.album_artist).find(Boolean) ||
      (new Set(tracks.map((t) => t.artist).filter(Boolean)).size > 1 ? "Varios artistas"
        : tracks.map((t) => t.artist).find(Boolean) || "");
    const lossless = tracks.length > 0 && tracks.every((t) => t.lossless);
    const cover = tracks.map((t) => t.cover_path).find(Boolean) || "";
    const meta = [plural(tracks.length, "pista", "pistas"),
      fmtDur(tracks.reduce((s, t) => s + (t.duration || 0), 0)),
      tracks.map((t) => t.date).find(Boolean) || "", formatLine(tracks)].filter(Boolean).join(" · ");
    return { type: "album", album: top.id, title: top.id || "(sin álbum)", artist, lossless, cover,
             tracks, meta, ctx: { kind: "album", album: top.id } };
  }
  if (top.type === "artist") {
    const page = await api.artist_page(top.id);
    const cover = (page.albums.find((a) => a.cover_path) || {}).cover_path || "";
    return { type: "artist", name: page.name, title: page.name || "(sin artista)", cover,
             albums: page.albums, tracks: page.tracks,
             meta: `${plural(page.albums.length, "álbum", "álbumes")} · ${plural(page.track_count, "pista", "pistas")} · ${fmtDur(page.total_duration)}`,
             ctx: { kind: "artist", name: page.name } };
  }
  const lists = await libData("playlists");
  const p = lists.find((x) => x.id === top.id) || { id: top.id, name: "Playlist", covers: [] };
  const tracks = await api.playlist_tracks(top.id);
  return { type: "playlist", pid: top.id, title: p.name, covers: p.covers, cover: p.covers[0] || "",
           tracks, meta: `${plural(tracks.length, "pista", "pistas")}${tracks.length ? " · " + fmtDur(tracks.reduce((s, t) => s + (t.duration || 0), 0)) : ""}`,
           ctx: { kind: "playlist", pid: top.id } };
}

// «FLAC · 44.1 kHz · 16-bit» si todo el álbum comparte formato.
function formatLine(tracks) {
  if (!tracks.length) return "";
  const key = (t) => [t.codec, t.sample_rate, t.bit_depth].join("|");
  if (new Set(tracks.map(key)).size > 1) return "Formatos mixtos";
  const t = tracks[0];
  const bits = [];
  if (t.codec) bits.push(String(t.codec).toUpperCase());
  if (t.sample_rate) bits.push(t.sample_rate / 1000 + " kHz");
  if (t.bit_depth) bits.push(t.bit_depth + "-bit");
  else if (t.bit_rate) bits.push(Math.round(t.bit_rate / 1000) + " kbps");
  return bits.join(" · ");
}

async function renderDetail(animate = true, src = null, keepScroll = false) {
  const tok = ++LIB.tok;
  const top = LIB.stack[LIB.stack.length - 1];
  // La portada de origen se mide ANTES de repintar (luego ya no existe).
  const from = src && !LIB_REDUCE ? flySource(src) : null;
  const m = await detailModel(top);
  if (tok !== LIB.tok) return;
  top.title = m.title;
  LIB.model = m;
  LIB.ctx = m.ctx;

  const kick = { album: "Álbum", artist: "Artista", playlist: "Playlist", smart: "Playlist inteligente" }[m.type];
  let cover, sub = "", body;
  if (m.type === "album") {
    cover = `<div class="lb-dcover"><span class="fy-vin"><span class="fy-vin-label" data-cover="${escapeHtml(m.cover)}"></span></span>
             <span class="fy-sleeve" id="lb-dcv" data-cover="${escapeHtml(m.cover)}"></span></div>`;
    sub = `${m.artist ? artistLink(m.artist) : ""}${m.lossless ? '<span class="lb-q">LOSSLESS</span>' : ""}`;
    body = m.tracks.length ? trackTable(m.tracks, "album") : '<div class="lb-empty">Este álbum no tiene pistas.</div>';
  } else if (m.type === "artist") {
    cover = `<div class="lb-dcover round"><span class="lb-dplate"></span>
             <span class="lb-dround" id="lb-dcv" data-cover="${escapeHtml(m.cover)}"></span></div>`;
    body = `<section class="lb-sec"><div class="fy-sec-h"><h3>Discografía</h3><span class="fy-rule"></span></div>
              <div class="lb-agrid">${m.albums.map(albumCard).join("")}</div></section>
            <section class="lb-sec"><div class="fy-sec-h"><h3>Canciones</h3><span class="fy-rule"></span></div>
              ${trackTable(m.tracks, "artist")}</section>`;
  } else if (m.type === "smart") {
    // Como una playlist, pero sin reordenar/quitar: su contenido lo deciden las reglas.
    cover = `<div class="lb-dcover mos">${mosaicHTML(m.covers).replace('class="lb-mosaic', 'id="lb-dcv" class="lb-mosaic')}</div>`;
    sub = `<span class="lb-q lb-q-smart">INTELIGENTE</span><span class="lb-smart-sum">${escapeHtml(m.summary)}</span>`;
    body = m.tracks.length ? trackTable(m.tracks, "songs")
      : '<div class="lb-empty">Ninguna canción cumple estas reglas todavía. Edítalas desde «⋯».</div>';
  } else {
    cover = `<div class="lb-dcover mos">${mosaicHTML(m.covers).replace('class="lb-mosaic', 'id="lb-dcv" class="lb-mosaic')}</div>`;
    body = m.tracks.length ? trackTable(m.tracks, "playlist")
      : '<div class="lb-empty">Esta playlist está vacía. Añade canciones con el botón ＋ de cualquier lista.</div>';
  }
  const acts = {
    album: `<button class="lb-btn pri" data-act="play"><i class="ph-bold ph-play"></i>Reproducir álbum</button>
            <button class="lb-btn" data-act="shuffle"><i class="ph-bold ph-shuffle"></i>Aleatorio</button>
            <button class="lb-btn" data-act="addpl"><i class="ph-bold ph-plus"></i>Añadir a playlist</button>
            <button class="lb-btn ico" data-act="more" title="Más opciones" aria-label="Más opciones"><i class="ph-bold ph-dots-three"></i></button>`,
    artist: `<button class="lb-btn pri" data-act="play"><i class="ph-bold ph-play"></i>Reproducir todo</button>
             <button class="lb-btn" data-act="shuffle"><i class="ph-bold ph-shuffle"></i>Aleatorio</button>
             <button class="lb-btn" data-act="addpl"><i class="ph-bold ph-plus"></i>Añadir a playlist</button>`,
    playlist: `<button class="lb-btn pri" data-act="play"${m.tracks.length ? "" : " disabled"}><i class="ph-bold ph-play"></i>Reproducir</button>
               <button class="lb-btn" data-act="shuffle"${m.tracks.length ? "" : " disabled"}><i class="ph-bold ph-shuffle"></i>Aleatorio</button>
               <button class="lb-btn" data-act="export"><i class="ph-bold ph-export"></i>Exportar</button>
               <button class="lb-btn ico" data-act="more" title="Más opciones" aria-label="Más opciones"><i class="ph-bold ph-dots-three"></i></button>`,
    // smart-* los atiende smart.js; shuffle y addpl son los genéricos (usan m.tracks).
    smart: `<button class="lb-btn pri" data-act="smart-play"${m.tracks.length ? "" : " disabled"}><i class="ph-bold ph-play"></i>Reproducir</button>
            <button class="lb-btn" data-act="shuffle"${m.tracks.length ? "" : " disabled"}><i class="ph-bold ph-shuffle"></i>Aleatorio</button>
            <button class="lb-btn" data-act="addpl"${m.tracks.length ? "" : " disabled"}><i class="ph-bold ph-plus"></i>Añadir a playlist</button>
            <button class="lb-btn" data-act="smart-edit"><i class="ph-bold ph-sliders-horizontal"></i>Editar reglas</button>
            <button class="lb-btn ico" data-act="smart-more" title="Más opciones" aria-label="Más opciones"><i class="ph-bold ph-dots-three"></i></button>`,
  }[m.type];

  const view = $("#lib-view");
  const y = window.scrollY;
  releaseCovers(view);
  view.innerHTML = `
    <button class="lb-back" data-back aria-label="Volver a ${escapeHtml(backLabel())}">
      <span class="ar"><i class="ph-bold ph-caret-left"></i></span>
      <span class="lbl"><small>Volver a</small>${escapeHtml(backLabel())}</span>
    </button>
    <section class="lb-hero">
      <div class="lb-hero-bg" data-cover="${escapeHtml(m.cover || "")}" aria-hidden="true"></div>
      <div class="lb-hero-grid">
        ${cover}
        <div class="lb-dinfo">
          <span class="lb-kick"><i></i>${kick}</span>
          <h1 class="lb-dtitle" id="lb-dtitle">${escapeHtml(m.title)}</h1>
          ${sub ? `<div class="lb-dsub">${sub}</div>` : ""}
          <div class="lb-dmeta">${escapeHtml(m.meta)}</div>
          <div class="lb-dacts">${acts}</div>
        </div>
      </div>
    </section>
    <div class="lb-body">${body}</div>`;
  view.querySelectorAll("[data-cover]").forEach((el) => {
    // La portada grande y el fondo se piden ya (están a la vista).
    if (el.closest(".lb-hero")) { if (el.dataset.cover) fetchCover(el, el.dataset.cover); }
    else lazyCover(el, el.dataset.cover);
  });
  markPlaying();
  fitLibTitle();
  if (keepScroll) window.scrollTo({ top: y, behavior: "instant" });
  else window.scrollTo({ top: 0, behavior: "instant" });
  if (animate) libReplay();
  if (from) flyCover(from, m.type === "artist");
}

// Título gigante: una línea si cabe con buen tamaño; si no, se parte.
function fitLibTitle() {
  const h = $("#lb-dtitle");
  if (!h || !h.parentElement.clientWidth) return;
  const W = h.parentElement.clientWidth;
  h.style.whiteSpace = "nowrap";
  h.style.fontSize = "100px";
  const s = Math.min(96, (100 * W) / (h.scrollWidth || 1));
  if (s >= 40) h.style.fontSize = s + "px";
  else {
    h.style.whiteSpace = "normal";
    h.style.fontSize = Math.max(34, Math.min(56, W / 9)) + "px";
  }
}

// --- Transición de la portada (elemento compartido) --------------------------------
// La portada pulsada «vuela» desde la tarjeta hasta la cabecera del detalle.

function flySource(src) {
  const el = src.querySelector ? (src.matches(".lb-src") ? src : src.querySelector(".lb-src")) : null;
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (!r.width) return null;
  const clone = el.cloneNode(true);
  clone.removeAttribute("id");
  clone.classList.remove("lb-src");
  return { rect: r, node: clone, bg: getComputedStyle(el).backgroundImage };
}

function flyCover(from, round) {
  const dst = $("#lb-dcv");
  if (!dst) return;
  const to = dst.getBoundingClientRect();
  if (!to.width) return;
  const c = from.node;
  c.classList.add("lb-clone");
  if (round) c.classList.add("round");
  if (from.bg && from.bg !== "none") c.style.backgroundImage = from.bg;
  Object.assign(c.style, { left: to.left + "px", top: to.top + "px", width: to.width + "px", height: to.height + "px" });
  document.body.appendChild(c);
  dst.style.visibility = "hidden";
  c.animate(
    [{ transform: `translate(${from.rect.left - to.left}px,${from.rect.top - to.top}px) scale(${from.rect.width / to.width})` },
     { transform: "none" }],
    { duration: 320, easing: "cubic-bezier(.2,.8,.2,1)" }
  ).finished.then(() => {
    dst.style.visibility = "";
    c.remove();
  });
}
