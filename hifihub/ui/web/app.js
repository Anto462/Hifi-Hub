"use strict";

// --- Bus de eventos (Python -> JS). events.py invoca window.hub._receive(...) ---
window.hub = {
  _handlers: {},
  on(channel, cb) {
    (this._handlers[channel] ||= []).push(cb);
  },
  _receive(channel, payload) {
    (this._handlers[channel] || []).forEach((cb) => cb(payload));
  },
};

const $ = (sel) => document.querySelector(sel);
let PROFILES = [];
let apiReady = false;

// pywebview inyecta window.pywebview.api de forma asíncrona.
window.addEventListener("pywebviewready", () => {
  apiReady = true;
  init();
});

function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", isError);
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), 3200);
}

// Diálogo de confirmación (promesa: resuelve true si el usuario acepta).
function confirmDialog({ title = "¿Confirmar?", msg = "", okLabel = "Eliminar" } = {}) {
  const modal = $("#confirm-modal");
  $("#confirm-title").textContent = title;
  $("#confirm-msg").textContent = msg;
  $("#btn-confirm-ok").textContent = okLabel;
  modal.hidden = false;
  return new Promise((resolve) => {
    const onKey = (e) => {
      if (e.key === "Escape") done(false);
      else if (e.key === "Enter") done(true);
    };
    const done = (val) => {
      modal.hidden = true;
      $("#btn-confirm-ok").onclick = null;
      $("#btn-confirm-cancel").onclick = null;
      document.removeEventListener("keydown", onKey, true);
      resolve(val);
    };
    $("#btn-confirm-ok").onclick = () => done(true);
    $("#btn-confirm-cancel").onclick = () => done(false);
    // Captura: se adelanta al Esc global para no ocultar el modal sin resolver.
    document.addEventListener("keydown", onKey, true);
  });
}

async function init() {
  setupTabs();
  PROFILES = await window.pywebview.api.list_profiles();
  const templates = await window.pywebview.api.list_templates();
  const cfg = await window.pywebview.api.get_config();

  fillSelect($("#profile"), PROFILES.map((p) => p.name));
  fillSelect($("#cfg-profile"), PROFILES.map((p) => p.name));
  fillSelect($("#template"), templates);
  $("#profile").value = cfg.default_profile;
  onProfileChange();

  // Ajustes
  $("#cfg-dir").value = cfg.download_dir;
  $("#cfg-profile").value = cfg.default_profile;
  $("#cfg-concurrency").value = cfg.batch_concurrency;
  $("#cfg-pacing").value = cfg.download_pacing ?? 0;
  $("#cfg-enrich").checked = !!cfg.enrich_library;
  $("#cfg-acoustid").value = cfg.acoustid_api_key || "";
  $("#cfg-sp-id").value = cfg.spotify_client_id || "";
  $("#cfg-sp-secret").value = cfg.spotify_client_secret || "";
  $("#enrich").checked = !!cfg.enrich_library;

  $("#cfg-exclusive").checked = !!cfg.wasapi_exclusive;
  // Los ajustes finos del dispositivo solo aplican en exclusivo: se muestran con él.
  $("#cfg-keepalive").checked = cfg.device_keepalive !== false;
  $("#cfg-waitopen").value = cfg.device_wait_open ?? 0.3;
  $("#cfg-limiter").checked = !!cfg.limiter_enabled;
  $("#cfg-listening").checked = !!cfg.listening_mode;
  SONGS_PER_PAGE = cfg.songs_per_page || 15;
  $("#songs-per-page").value = String(SONGS_PER_PAGE);
  GRID_PER_PAGE = cfg.grid_per_page || 25;
  $("#grid-per-page").value = String(GRID_PER_PAGE);
  $("#grid-per-page-2").value = String(GRID_PER_PAGE);
  updateWasapiOpts();
  $("#cfg-gain").checked = !!cfg.apply_replaygain;
  $("#sponsorblock").checked = !!cfg.sponsorblock;
  $("#cfg-bpm").checked = !!cfg.analyze_bpm_key;
  $("#multi-source").checked = !!cfg.multi_source;
  applySources(cfg.download_sources || "youtube");
  SAVED_DEVICE = cfg.audio_device || "auto";
  // Controles de nivelación (vista Reproduciendo).
  $("#rg-mode").value = cfg.replaygain_mode || "track";
  $("#rg-preamp").value = cfg.replaygain_preamp ?? 3;
  $("#rg-preamp-val").textContent = "+" + ($("#rg-preamp").value) + " dB";

  $("#cfg-autoupd").checked = cfg.auto_check_updates !== false;
  $("#cfg-channel").value = cfg.ytdlp_channel || "stable";

  wireEvents();
  wireSources();
  wireDownloadEvents();
  wireLibTools();
  wireWasapiOpts();
  wireShortcuts();
  wireLibrary();
  wirePlaylists();
  wireContextMenu();
  wirePlayer();
  wireUpdates();
  wireImport();
  loadForYou();      // «Para ti» es la vista de arranque
  loadAlbums();

  // Chequeo silencioso al arrancar (solo avisa; instalar requiere un clic).
  if (cfg.auto_check_updates !== false) autoCheckUpdates();
}

// ============ FUENTES DE DESCARGA ============
// Una sola marcada -> se descarga sí o sí de esa. Las dos -> gana la de mayor
// calidad real (archive.org tiene FLAC lossless; YouTube es lossy).

function selectedSources() {
  const out = [];
  if ($("#src-youtube").checked) out.push("youtube");
  if ($("#src-archive").checked) out.push("archive");
  return out;
}

function applySources(csv) {
  const set = String(csv || "youtube").split(",").map((s) => s.trim());
  $("#src-youtube").checked = set.includes("youtube");
  $("#src-archive").checked = set.includes("archive");
  updateSourceHint();
}

function updateSourceHint() {
  const sel = selectedSources();
  const hint = $("#src-hint");
  if (sel.length === 0) {
    hint.textContent =
      "Marca al menos una fuente (si no, se usará YouTube por defecto).";
  } else if (sel.length === 2) {
    hint.textContent =
      "Ambas: se busca en las dos y se descarga la de mayor calidad real (un FLAC de archive.org gana a cualquier stream de YouTube).";
  } else if (sel[0] === "youtube") {
    hint.textContent = "Solo YouTube: se descarga de ahí sin buscar en otras fuentes.";
  } else {
    hint.textContent =
      "Solo archive.org: lossless real (conciertos de la Live Music Archive, dominio público y licencias libres). Si la canción no está ahí, la descarga falla en vez de caer a YouTube.";
  }
}

function wireSources() {
  const save = async () => {
    updateSourceHint();
    await window.pywebview.api.save_config({
      download_sources: selectedSources().join(","),
    });
  };
  $("#src-youtube").addEventListener("change", save);
  $("#src-archive").addEventListener("change", save);
}

// ============ IMPORTAR ============
let IMPORT_INPUTS = [];

function wireImport() {
  $("#btn-imp-files").addEventListener("click", async () => {
    const files = await window.pywebview.api.choose_audio_files();
    if (files && files.length) {
      IMPORT_INPUTS = files;
      $("#imp-selection").textContent = `${files.length} archivo(s) seleccionado(s).`;
    }
  });
  $("#btn-imp-folder").addEventListener("click", async () => {
    const folder = await window.pywebview.api.choose_folder();
    if (folder) {
      IMPORT_INPUTS = [folder];
      $("#imp-selection").textContent = "Carpeta: " + folder;
    }
  });
  $("#btn-imp-dest").addEventListener("click", async () => {
    const folder = await window.pywebview.api.choose_folder();
    if (folder) $("#imp-dest").value = folder;
  });
  $("#btn-imp-start").addEventListener("click", async () => {
    if (!IMPORT_INPUTS.length) return toast("Elige archivos o una carpeta primero.", true);
    const res = await window.pywebview.api.start_import({
      inputs: IMPORT_INPUTS,
      dest: $("#imp-dest").value.trim() || null,
      enrich: $("#imp-enrich").checked,
      replaygain: $("#imp-replaygain").checked,
      convert_flac: $("#imp-convert").checked,
      move: $("#imp-move").checked,
    });
    if (!res.ok) return toast(res.error, true);
    const box = $("#imp-jobs");
    box.innerHTML = `<div class='job-title'>Importando ${res.total} archivo(s)…</div>` +
      `<div class='bar'><span></span></div><div class='job-meta'>0/${res.total}</div>`;
    IMPORT_TOTAL = res.total;
  });

  window.hub.on("import:item", (p) => {
    const box = $("#imp-jobs");
    const meta = box.querySelector(".job-meta");
    const bar = box.querySelector(".bar > span");
    if (meta && IMPORT_TOTAL) {
      meta.textContent = `${p.done}/${p.total}` + (p.ok ? "" : ` · error en ${(p.error||"").slice(0,50)}`);
      if (bar) bar.style.width = (p.done / p.total) * 100 + "%";
    }
  });
  window.hub.on("import:complete", (p) => {
    const box = $("#imp-jobs");
    const meta = box.querySelector(".job-meta");
    if (meta) meta.textContent = `Importadas ${p.imported} · omitidas ${p.skipped} · errores ${p.errors}`;
    const bar = box.querySelector(".bar > span");
    if (bar) bar.style.width = "100%";
    toast(`Importación completa: ${p.imported} añadidas.`);
    rescanLibrary();
  });
}
let IMPORT_TOTAL = 0;

// ============ ACTUALIZACIONES ============
// Los ajustes del dispositivo solo tienen efecto en modo exclusivo: se revelan
// junto a esa casilla para que quede claro que viajan con ella.
function updateWasapiOpts() {
  const on = $("#cfg-exclusive").checked;
  $("#wasapi-opts").hidden = !on;
}

function wireWasapiOpts() {
  $("#cfg-exclusive").addEventListener("change", updateWasapiOpts);
}

function wireUpdates() {
  $("#btn-update-dismiss").addEventListener("click", () => ($("#update-banner").hidden = true));
  $("#btn-update-apply").addEventListener("click", () => applyUpdate($("#btn-update-apply")));
  $("#btn-upd-check").addEventListener("click", checkUpdatesManual);
  $("#btn-upd-apply").addEventListener("click", () => applyUpdate($("#btn-upd-apply")));
  // Cambiar de canal se guarda al instante y vuelve a comprobar en ese canal.
  $("#cfg-channel").addEventListener("change", async () => {
    await window.pywebview.api.save_config({ ytdlp_channel: $("#cfg-channel").value });
    checkUpdatesManual();
  });
}

function updChannel() {
  return $("#cfg-channel").value || "stable";
}

async function autoCheckUpdates() {
  try {
    const st = await window.pywebview.api.check_updates();
    if (st.ok && st.update_available) {
      $("#update-banner-text").textContent =
        `Actualización del motor de descargas disponible (yt-dlp ${st.current} → ${st.latest}).`;
      $("#update-banner").hidden = false;
    }
  } catch (e) {
    /* sin red: silencio */
  }
}

async function checkUpdatesManual() {
  $("#upd-status").textContent = "Consultando…";
  const st = await window.pywebview.api.check_updates(updChannel());
  if (!st.ok) {
    $("#upd-status").textContent = "Sin conexión: " + (st.error || "");
    return;
  }
  const chLabel = st.channel === "nightly" ? "nightly" : "estable";
  $("#upd-info").textContent =
    `App v${st.app_version} · motor yt-dlp ${st.current} (${st.source}, canal ${chLabel})` +
    (st.update_available ? ` · disponible: ${st.latest}` : " · al día");
  $("#upd-status").textContent = st.update_available ? "" : "No hay actualizaciones.";
  $("#btn-upd-apply").hidden = !st.update_available;
  $("#btn-upd-apply").textContent =
    st.channel === "nightly" ? "Instalar nightly" : "Actualizar motor";
}

async function applyUpdate(btn) {
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "Actualizando…";
  const res = await window.pywebview.api.apply_update(updChannel());
  btn.disabled = false;
  btn.textContent = prev;
  if (!res.ok) return toast(res.error, true);
  toast(res.message || "Actualizado.");
  if (res.restart_required) {
    $("#update-banner-text").textContent = "Actualizado. Reinicia la aplicación para aplicar el nuevo motor.";
    $("#update-banner").hidden = false;
    $("#btn-update-apply").hidden = true;
    $("#btn-upd-apply").hidden = true;
    $("#upd-status").textContent = "Reinicia la aplicación para aplicar.";
  }
}

function fillSelect(sel, values) {
  sel.innerHTML = "";
  for (const v of values) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    sel.appendChild(opt);
  }
}

let SAVED_DEVICE = "auto";
let devicesLoaded = false;

function switchView(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === name)
  );
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  const view = $("#view-" + name);
  if (view) view.classList.add("active");
  updateMiniVisibility();   // la barra depende de la vista activa
  if (name === "parati") loadForYou();
  if (name === "ajustes" && !devicesLoaded) loadDevices();
  if (name === "estudio" && !studioLoaded) initStudio();
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });
  // El wordmark hace de "casa": es el gesto que la gente intenta por instinto.
  const wm = $("#wordmark");
  if (wm) {
    wm.addEventListener("click", () => switchView("parati"));
    wm.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        switchView("parati");
      }
    });
  }
}

async function loadDevices() {
  // Enumerar dispositivos arranca libmpv: solo al abrir Ajustes por primera vez.
  devicesLoaded = true;
  try {
    const devices = await window.pywebview.api.player_devices();
    const sel = $("#cfg-device");
    for (const d of devices) {
      if (d.name === "auto") continue;
      const opt = document.createElement("option");
      opt.value = d.name;
      opt.textContent = d.description || d.name;
      sel.appendChild(opt);
    }
    sel.value = SAVED_DEVICE;
    if (sel.selectedIndex < 0) sel.value = "auto";
  } catch (e) {
    /* sin dispositivos: se queda en auto */
  }
}

function onProfileChange() {
  const prof = PROFILES.find((p) => p.name === $("#profile").value);
  $("#profile-desc").textContent = prof ? prof.description : "";
  // Resampleo solo tiene sentido en perfiles que recodifican (flac/alac).
  $("#resample-row").hidden = !(prof && prof.recodes && prof.name !== "mp3");
}

function wireEvents() {
  $("#profile").addEventListener("change", onProfileChange);

  $("#btn-folder").addEventListener("click", async () => {
    const folder = await window.pywebview.api.choose_folder();
    if (folder) $("#dir").value = folder;
  });
  $("#btn-cfg-folder").addEventListener("click", async () => {
    const folder = await window.pywebview.api.choose_folder();
    if (folder) $("#cfg-dir").value = folder;
  });

  $("#btn-preview").addEventListener("click", previewDownload);
  $("#btn-download").addEventListener("click", startDownload);
  $("#btn-batch").addEventListener("click", startBatch);
  $("#btn-save-cfg").addEventListener("click", saveConfig);
}

async function startBatch() {
  const file = await window.pywebview.api.choose_txt();
  if (!file) return;
  const res = await window.pywebview.api.start_batch({
    file,
    dir: $("#dir").value,
    profile: $("#profile").value,
    template: $("#template").value,
    enrich: $("#enrich").checked,
  });
  if (!res.ok) return toast(res.error, true);
  const jobs = $("#jobs");
  const card = document.createElement("div");
  card.className = "job";
  card.id = "job-batch";
  card.innerHTML = `
    <div class='job-title'>Lote: ${escapeHtml(file)}</div>
    <div class='bar'><span></span></div>
    <div class='job-meta'>0/${res.total}…</div>`;
  jobs.prepend(card);
}

async function previewDownload() {
  const url = $("#url").value.trim();
  if (!url) return toast("Introduce un enlace.", true);
  const box = $("#preview");
  box.hidden = false;
  box.innerHTML = "<div class='hint'>Analizando…</div>";
  const res = await window.pywebview.api.plan_audio(url, $("#dir").value, $("#template").value);
  if (!res.ok) {
    box.innerHTML = `<div class='hint'>Error: ${escapeHtml(res.error)}</div>`;
    return;
  }
  box.innerHTML =
    `<div class='hint'>${res.items.length} pista(s):</div>` +
    res.items
      .map(
        (it) =>
          `<div class='preview-item'><span class='preview-dur'>${fmtDur(it.duration)}</span>${escapeHtml(it.path)}</div>`
      )
      .join("");
}

async function startDownload() {
  const url = $("#url").value.trim();
  if (!url) return toast("Introduce un enlace o el nombre de una canción.", true);
  const res = await window.pywebview.api.start_download({
    url,
    dir: $("#dir").value,
    profile: $("#profile").value,
    template: $("#template").value,
    sample_rate: $("#sample-rate").value || null,
    bit_depth: $("#bit-depth").value || null,
    enrich: $("#enrich").checked,
    multi_source: $("#multi-source").checked,
    sources: selectedSources().join(","),
    sections: $("#sections").value.trim() || null,
    exact_cuts: $("#exact-cuts").checked,
    split_chapters: $("#split-chapters").checked,
    sponsorblock: $("#sponsorblock").checked,
  });
  if (!res.ok) return toast(res.error, true);
  createJobCard(res.job_id, url);
}

async function saveConfig() {
  await window.pywebview.api.save_config({
    download_dir: $("#cfg-dir").value,
    default_profile: $("#cfg-profile").value,
    batch_concurrency: $("#cfg-concurrency").value,
    download_pacing: $("#cfg-pacing").value,
    enrich_library: $("#cfg-enrich").checked,
    acoustid_api_key: $("#cfg-acoustid").value,
    spotify_client_id: $("#cfg-sp-id").value,
    spotify_client_secret: $("#cfg-sp-secret").value,
    wasapi_exclusive: $("#cfg-exclusive").checked,
    apply_replaygain: $("#cfg-gain").checked,
    analyze_bpm_key: $("#cfg-bpm").checked,
    auto_check_updates: $("#cfg-autoupd").checked,
  });
  await window.pywebview.api.set_listening_mode($("#cfg-listening").checked);
  // Ajustes del dispositivo: van por su propia API (aplican al reproductor vivo).
  await window.pywebview.api.set_device_options(
    $("#cfg-keepalive").checked,
    parseFloat($("#cfg-waitopen").value) || 0,
    $("#cfg-limiter").checked,
  );
  const dev = $("#cfg-device").value;
  if (dev !== SAVED_DEVICE) {
    await window.pywebview.api.player_set_device(dev);
    SAVED_DEVICE = dev;
  }
  $("#cfg-status").textContent = "Guardado ✓";
  setTimeout(() => ($("#cfg-status").textContent = ""), 2000);
}

// --- Tarjetas de descarga alimentadas por eventos ---
function createJobCard(jobId, url) {
  const jobs = $("#jobs");
  const card = document.createElement("div");
  card.className = "job";
  card.id = "job-" + jobId;
  card.innerHTML = `
    <div class='job-title'>${escapeHtml(url)}</div>
    <div class='bar'><span></span></div>
    <div class='job-meta'>En cola…</div>`;
  jobs.prepend(card);
}

function wireDownloadEvents() {
  window.hub.on("download:progress", (p) => {
    const card = $("#job-" + p.job_id);
    if (!card) return;
    if (typeof p.percent === "number") {
      card.querySelector(".bar > span").style.width = p.percent + "%";
    }
    const meta = card.querySelector(".job-meta");
    if (p.status === "starting") meta.textContent = "Iniciando…";
    else if (p.status === "searching") meta.textContent = p.stage || "Buscando fuentes…";
    else if (p.status === "downloading")
      meta.textContent = `${p.percent ?? "?"}% · ${p.speed || ""} · ETA ${fmtDur(p.eta)}`;
    else if (p.status === "finished") meta.textContent = p.stage || "Procesando…";
  });
  window.hub.on("download:sources", (p) => {
    const card = $("#job-" + p.job_id);
    if (!card) return;
    let box = card.querySelector(".sources");
    if (!box) {
      box = document.createElement("div");
      box.className = "sources";
      card.appendChild(box);
    }
    box.innerHTML =
      "<div class='hint'>Fuentes comparadas:</div>" +
      p.sources
        .map((s) => {
          const mark = s.chosen ? "▶ " : s.original ? "· " : "  ";
          const orig = s.original ? " (original)" : "";
          const sim = s.original ? "" : ` · ${Math.round(s.match * 100)}%`;
          const cls = s.chosen ? "src-chosen" : "";
          return `<div class='src-row ${cls}'>${mark}${escapeHtml(s.source)}${orig}: ${escapeHtml(s.quality)}${sim}</div>`;
        })
        .join("");
  });
  window.hub.on("download:complete", (p) => {
    const card = $("#job-" + p.job_id);
    if (!card) return;
    card.classList.add("done");
    card.querySelector(".bar > span").style.width = "100%";
    card.querySelector(".job-meta").textContent = "Completado ✓";
    // La biblioteca se actualiza sola tras cada descarga.
    rescanLibrary();
  });
  window.hub.on("download:error", (p) => {
    const card = $("#job-" + p.job_id);
    if (!card) return;
    card.classList.add("error");
    card.querySelector(".job-meta").textContent = "Error: " + p.error;
  });
  // Lotes
  window.hub.on("batch:item", (p) => {
    const card = $("#job-batch");
    if (!card) return;
    card.querySelector(".bar > span").style.width = (p.done / p.total) * 100 + "%";
    const errTxt = p.status === "error" ? ` · último error: ${p.error || "?"}` : "";
    card.querySelector(".job-meta").textContent = `${p.done}/${p.total}${errTxt}`;
  });
  window.hub.on("batch:complete", (p) => {
    const card = $("#job-batch");
    if (!card) return;
    card.classList.add(p.failed ? "error" : "done");
    card.querySelector(".bar > span").style.width = "100%";
    card.querySelector(".job-meta").textContent =
      `Lote terminado: ${p.ok} ok · ${p.failed} con error de ${p.total}`;
    rescanLibrary();
  });
}

// ============ BIBLIOTECA ============
let CURRENT_ALBUM = null;
let CURRENT_SUB = "albums";
let CURRENT_ARTIST_TRACKS = [];

// Rejillas de Álbumes y Artistas: mismo patrón que Canciones (filtro local +
// paginación). Su tamaño de página es propio: en una rejilla caben más tarjetas.
let ALBUMS = [], ALBUMS_VIEW = [], ALBUMS_PAGE = 0;
let ARTISTS = [], ARTISTS_VIEW = [], ARTISTS_PAGE = 0;
let GRID_PER_PAGE = 25;

// Canciones: lista completa en memoria, mostrada filtrada y paginada.
let SONGS = [];        // todo lo cargado (ya ordenado)
let SONGS_VIEW = [];   // lo que queda tras el filtro -> es lo que se reproduce
let SONGS_PAGE = 0;
let SONGS_PER_PAGE = 15;

function wireLibrary() {
  $("#btn-scan").addEventListener("click", () => rescanLibrary());

  // Canciones: UN solo listener delegado en el contenedor, en vez de uno por fila.
  // Se registra aquí (wireLibrary corre una vez) porque loadSongs se llama cada vez
  // que se entra a la subvista y acumularía listeners.
  $("#songs").addEventListener("dblclick", (e) => {
    const row = e.target.closest(".track-row");
    if (!row || row.dataset.i === undefined) return;
    // Se manda la lista visible completa (ya ordenada/filtrada) y la posición.
    window.pywebview.api.play_ids(SONGS_VIEW.map((t) => t.id), parseInt(row.dataset.i));
  });
  let songsFilterTimer;
  $("#songs-filter").addEventListener("input", () => {
    // Es local: se puede refrescar casi al instante sin castigar nada.
    clearTimeout(songsFilterTimer);
    songsFilterTimer = setTimeout(applySongsFilter, 120);
  });
  $("#songs-per-page").addEventListener("change", async (e) => {
    SONGS_PER_PAGE = parseInt(e.target.value) || 15;
    SONGS_PAGE = 0;
    renderSongsPage();
    await window.pywebview.api.save_config({ songs_per_page: SONGS_PER_PAGE });
  });
  // Rejillas: filtro local + paginación (mismo patrón que Canciones).
  let albTimer, artTimer;
  $("#albums-filter").addEventListener("input", () => {
    clearTimeout(albTimer);
    albTimer = setTimeout(() => applyAlbumsFilter(), 120);
  });
  $("#artists-filter").addEventListener("input", () => {
    clearTimeout(artTimer);
    artTimer = setTimeout(() => applyArtistsFilter(), 120);
  });
  // Un solo ajuste para las dos rejillas: los dos selectores se mantienen a la par.
  const onGridSize = async (value) => {
    GRID_PER_PAGE = parseInt(value) || 25;
    $("#grid-per-page").value = String(GRID_PER_PAGE);
    $("#grid-per-page-2").value = String(GRID_PER_PAGE);
    ALBUMS_PAGE = ARTISTS_PAGE = 0;
    if (CURRENT_SUB === "artists") renderArtistsPage();
    else renderAlbumsPage();
    await window.pywebview.api.save_config({ grid_per_page: GRID_PER_PAGE });
  };
  $("#grid-per-page").addEventListener("change", (e) => onGridSize(e.target.value));
  $("#grid-per-page-2").addEventListener("change", (e) => onGridSize(e.target.value));
  const goGrid = (which, delta) => {
    if (which === "albums") { ALBUMS_PAGE += delta; renderAlbumsPage(); }
    else { ARTISTS_PAGE += delta; renderArtistsPage(); }
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  $("#albums-prev").addEventListener("click", () => goGrid("albums", -1));
  $("#albums-next").addEventListener("click", () => goGrid("albums", 1));
  $("#artists-prev").addEventListener("click", () => goGrid("artists", -1));
  $("#artists-next").addEventListener("click", () => goGrid("artists", 1));

  $("#songs-prev").addEventListener("click", () => goSongsPage(-1));
  $("#songs-next").addEventListener("click", () => goSongsPage(1));

  let searchTimer;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => doSearch(e.target.value), 250);
  });

  // Subnavegación Álbumes / Artistas / Canciones
  document.querySelectorAll(".subtab").forEach((tab) => {
    tab.addEventListener("click", () => switchSub(tab.dataset.sub));
  });

  // Nombres de artista clicables en CUALQUIER vista (delegación global).
  document.addEventListener("click", (e) => {
    const link = e.target.closest(".artist-link");
    if (!link) return;
    e.stopPropagation();
    openArtist(link.dataset.artist === "" ? null : link.dataset.artist);
  });

  $("#btn-artist-back").addEventListener("click", () => {
    $("#artist-page").hidden = true;
    $("#artists").hidden = false;
    loadArtists(false);      // vuelve a la MISMA página, no a la 1
  });

  // Volver de la página de álbum al grid (y re-pintar por si se borró algo).
  $("#btn-album-back").addEventListener("click", () => loadAlbums(false));
}

function switchSub(name, load = true) {
  CURRENT_SUB = name;
  // Elegir una subpestaña sale de la búsqueda (si no, quedaría oculta detrás).
  if ($("#view-biblioteca").classList.contains("searching")) {
    $("#search").value = "";
    exitSearch();
  }
  document.querySelectorAll(".subtab").forEach((t) =>
    t.classList.toggle("active", t.dataset.sub === name)
  );
  document.querySelectorAll(".subview").forEach((v) => v.classList.remove("active"));
  $("#sub-" + name).classList.add("active");
  // Orden/filtro solo tienen sentido sobre álbumes o pistas.
  $("#lib-controls").hidden = !(name === "albums" || name === "songs");
  if (!load) return;
  if (name === "albums") loadAlbums();
  else if (name === "artists") loadArtists();
  else if (name === "songs") loadSongs();
  else if (name === "playlists") loadPlaylists();
}

async function rescanLibrary() {
  $("#lib-stats").textContent = "Escaneando…";
  const res = await window.pywebview.api.scan_library();
  $("#lib-stats").textContent = `${res.tracks} pistas · ${res.albums} álbumes · ${res.artists} artistas`;
  refreshLibrary();
}

function refreshLibrary() {
  if (CURRENT_SUB === "albums") loadAlbums();
  else if (CURRENT_SUB === "artists") loadArtists();
  else if (CURRENT_SUB === "songs") loadSongs();
}

function artistLink(name) {
  const label = name || "(sin artista)";
  return `<span class="artist-link" data-artist="${escapeHtml(name || "")}">${escapeHtml(label)}</span>`;
}

function plusBtn(trackId) {
  return (
    `<button class="row-add" data-id="${trackId}" title="Añadir a playlist"><i class="ph-bold ph-plus"></i></button>` +
    `<button class="row-ctx" data-id="${trackId}" title="Más acciones"><i class="ph-bold ph-dots-three"></i></button>`
  );
}

// ============ MENÚ CONTEXTUAL POR PISTA (⋯) ============
function wireContextMenu() {
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".row-ctx");
    const menu = $("#ctxmenu");
    if (!btn) {
      if (!e.target.closest("#ctxmenu")) menu.hidden = true;
      return;
    }
    e.stopPropagation();
    openContextMenu(btn, parseInt(btn.dataset.id));
  });
  $("#btn-edit-cancel").addEventListener("click", () => ($("#edit-modal").hidden = true));
  $("#btn-edit-save").addEventListener("click", saveEdit);

  // Con position:fixed el menú no acompaña al scroll, así que se cierra.
  window.addEventListener("scroll", closeMenus, { passive: true });
  window.addEventListener("resize", closeMenus);
  // Esc cierra menús y el modal de edición. El de confirmación NO se toca aquí:
  // gestiona su propio Esc para resolver la promesa como "cancelar" (si se
  // ocultara sin resolver, el await de quien llamó se quedaría colgado).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    closeMenus();
    $("#edit-modal").hidden = true;
  });
}

// Coloca un menú flotante junto a su botón SIN salirse de la pantalla.
// El menú ya debe estar visible (hidden=false) para poder medir su tamaño real.
const MENU_GAP = 4;
const MENU_MARGIN = 8;

function placeMenu(menu, anchorEl) {
  const a = anchorEl.getBoundingClientRect();
  const m = menu.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Vertical: debajo del botón y, si no cabe, volteado encima.
  let top = a.bottom + MENU_GAP;
  if (top + m.height > vh - MENU_MARGIN) {
    const above = a.top - m.height - MENU_GAP;
    top = above >= MENU_MARGIN ? above : Math.max(MENU_MARGIN, vh - m.height - MENU_MARGIN);
  }
  // Horizontal: alineado a la izquierda del botón, acotado a los dos bordes.
  let left = Math.min(a.left, vw - m.width - MENU_MARGIN);
  left = Math.max(MENU_MARGIN, left);

  menu.style.top = top + "px";
  menu.style.left = left + "px";
}

function closeMenus() {
  $("#ctxmenu").hidden = true;
  const pl = $("#plmenu");
  if (pl) pl.hidden = true;
}

function openContextMenu(btn, trackId) {
  const menu = $("#ctxmenu");
  const actions = [
    ["Reproducir ahora", () => window.pywebview.api.play_track(trackId)],
    ["Añadir a la cola", async () => {
      const res = await window.pywebview.api.add_to_queue([trackId]);
      if (res.ok) toast("Añadida a la cola");
    }],
    ["Añadir a playlist…", () => openPlaylistMenuFor(btn, trackId)],
    ["Editar información", () => openEdit(trackId)],
    ["Mostrar en carpeta", () => window.pywebview.api.reveal_in_folder(trackId)],
    ["Quitar de la biblioteca", async () => {
      await window.pywebview.api.remove_from_library(trackId);
      toast("Quitada de la biblioteca (el archivo sigue en disco).");
      refreshLibrary();
    }],
    ["Eliminar del disco…", async () => {
      const t = await window.pywebview.api.get_track(trackId);
      const ok = await confirmDialog({
        title: "Eliminar canción",
        msg: `Se enviará a la Papelera de reciclaje «${(t && t.title) || "esta pista"}» y se quitará de la biblioteca. ¿Continuar?`,
      });
      if (!ok) return;
      const res = await window.pywebview.api.delete_track(trackId);
      toast(res.ok ? "Canción eliminada (enviada a la Papelera)." : (res.error || "No se pudo eliminar."), !res.ok);
      refreshLibrary();
    }],
  ];
  menu.innerHTML = "";
  actions.forEach(([label, fn]) => {
    const item = document.createElement("div");
    item.className = "plmenu-item";
    item.textContent = label;
    item.addEventListener("click", () => {
      menu.hidden = true;
      fn();
    });
    menu.appendChild(item);
  });
  menu.hidden = false;
  placeMenu(menu, btn);
}

// --- Modal de edición ---
let EDITING_ID = null;

async function openEdit(trackId) {
  const t = await window.pywebview.api.get_track(trackId);
  if (!t) return toast("Pista no encontrada.", true);
  EDITING_ID = trackId;
  $("#ed-title").value = t.title || "";
  $("#ed-artist").value = t.artist || "";
  $("#ed-album_artist").value = t.album_artist || "";
  $("#ed-album").value = t.album || "";
  $("#ed-track_number").value = t.track_number || "";
  $("#ed-date").value = t.date || "";
  $("#ed-genre").value = t.genre || "";
  $("#edit-status").textContent = "";
  $("#edit-modal").hidden = false;
}

async function saveEdit() {
  if (EDITING_ID == null) return;
  $("#edit-status").textContent = "Guardando…";
  const fields = {
    title: $("#ed-title").value,
    artist: $("#ed-artist").value,
    album_artist: $("#ed-album_artist").value,
    album: $("#ed-album").value,
    track_number: $("#ed-track_number").value,
    date: $("#ed-date").value,
    genre: $("#ed-genre").value,
  };
  const res = await window.pywebview.api.update_track(EDITING_ID, fields);
  if (!res.ok) {
    $("#edit-status").textContent = res.error;
    return;
  }
  $("#edit-modal").hidden = true;
  toast("Información actualizada.");
  refreshLibrary();
}

// ============ PLAYLISTS ============
let CURRENT_PLAYLIST = null;

function wirePlaylists() {
  $("#btn-pl-create").addEventListener("click", async () => {
    const res = await window.pywebview.api.playlist_create($("#pl-new-name").value);
    if (!res.ok) return toast(res.error, true);
    $("#pl-new-name").value = "";
    loadPlaylists();
  });
  $("#btn-pl-import").addEventListener("click", async () => {
    const res = await window.pywebview.api.playlist_import();
    if (!res.ok) return res.error !== "Cancelado." && toast(res.error, true);
    toast(`Importada (${res.added} pistas). ${res.note}`);
    loadPlaylists();
  });
  $("#btn-pl-back").addEventListener("click", () => {
    $("#playlist-detail").hidden = true;
    $("#playlists").hidden = false;
    loadPlaylists();
  });

  // Menú flotante "Añadir a playlist" (botón ＋ de cualquier pista).
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".row-add");
    const menu = $("#plmenu");
    if (!btn) {
      if (!e.target.closest("#plmenu")) menu.hidden = true;
      return;
    }
    e.stopPropagation();
    openPlaylistMenuFor(btn, btn.dataset.id);
  });
}

async function openPlaylistMenuFor(anchorEl, trackId) {
  return openPlaylistMenuForMany(anchorEl, [trackId]);
}

// Menú «Añadir a…» para UNA pista o una colección entera (álbum, artista, mezcla).
// Ofrece además crear una playlist nueva con esas pistas en un solo paso.
async function openPlaylistMenuForMany(anchorEl, trackIds, label = "") {
  const ids = (trackIds || []).filter((t) => t !== undefined && t !== null);
  if (!ids.length) return toast("No hay pistas que añadir.", true);
  const menu = $("#plmenu");
  const lists = await window.pywebview.api.playlists_list();

  const many = ids.length > 1;
  menu.innerHTML =
    `<div class='hint'>${many ? `Añadir ${ids.length} pistas a…` : "Añadir a…"}</div>` +
    lists.map((p) => `<div class='plmenu-item' data-pid='${p.id}'>${escapeHtml(p.name)}</div>`).join("") +
    `<div class='plmenu-item plmenu-new' data-new='1'><i class="ph-bold ph-plus"></i> Nueva playlist…</div>`;
  menu.hidden = false;
  placeMenu(menu, anchorEl);

  menu.querySelectorAll(".plmenu-item").forEach((item) => {
    item.addEventListener("click", async () => {
      menu.hidden = true;
      if (item.dataset.new) {
        const name = await promptDialog({
          title: "Nueva playlist",
          msg: `Se creará con ${ids.length} pista(s).`,
          value: label,
        });
        if (!name) return;
        const res = await window.pywebview.api.playlist_create_with(name, ids);
        if (!res.ok) return toast(res.error, true);
        toast(`«${res.name}» creada con ${res.added} pista(s).`);
        if (CURRENT_SUB === "playlists") loadPlaylists();
        return;
      }
      const res = await window.pywebview.api.playlist_add_many(item.dataset.pid, ids);
      const dest = item.textContent.trim();
      if (res.skipped) {
        toast(`${res.added} añadida(s) a ${dest} · ${res.skipped} ya estaba(n).`);
      } else {
        toast(`${res.added} añadida(s) a ${dest}.`);
      }
    });
  });
}

async function loadPlaylists() {
  const lists = await window.pywebview.api.playlists_list();
  $("#playlist-detail").hidden = true;
  const box = $("#playlists");
  box.hidden = false;
  box.innerHTML = lists.length
    ? lists
        .map(
          (p) =>
            `<div class='track-row pl-row' data-pid='${p.id}' data-name='${escapeHtml(p.name)}'>
               <span class='track-title'>${escapeHtml(p.name)}</span>
               <span class='hint'>${p.n} pista(s)</span>
             </div>`
        )
        .join("")
    : "<div class='hint'>Sin playlists. Crea una arriba y añade canciones con el botón ＋.</div>";
  box.querySelectorAll(".pl-row").forEach((row) => {
    row.addEventListener("click", () => openPlaylist(parseInt(row.dataset.pid), row.dataset.name));
  });
}

async function openPlaylist(pid, name) {
  CURRENT_PLAYLIST = { id: pid, name };
  const tracks = await window.pywebview.api.playlist_tracks(pid);
  $("#playlists").hidden = true;
  $("#playlist-detail").hidden = false;
  $("#pl-rename-row").hidden = true;
  $("#pl-name").textContent = name;
  $("#pl-meta").textContent =
    `${tracks.length} pista(s) · ${fmtDur(tracks.reduce((s, t) => s + (t.duration || 0), 0))}`;

  $("#btn-pl-play").onclick = () => window.pywebview.api.play_playlist(pid, 0);
  $("#btn-pl-export").onclick = async () => {
    const res = await window.pywebview.api.playlist_export(pid);
    if (res.ok) toast("Exportada: " + res.path);
    else if (res.error !== "Cancelado.") toast(res.error, true);
  };
  $("#btn-pl-delete").onclick = async () => {
    await window.pywebview.api.playlist_delete(pid);
    toast("Playlist eliminada (las canciones siguen en la biblioteca).");
    loadPlaylists();
  };
  $("#btn-pl-rename").onclick = () => {
    $("#pl-rename-row").hidden = false;
    $("#pl-rename-input").value = CURRENT_PLAYLIST.name;
    $("#pl-rename-input").focus();
  };
  $("#btn-pl-rename-ok").onclick = async () => {
    const res = await window.pywebview.api.playlist_rename(pid, $("#pl-rename-input").value);
    if (!res.ok) return toast(res.error, true);
    openPlaylist(pid, $("#pl-rename-input").value.trim());
  };

  $("#pl-tracks").innerHTML = tracks.length
    ? tracks
        .map(
          (t, i) =>
            `<div class='track-row' data-i='${i}' data-track-id='${t.id}'>
               <span class='track-num'>${i + 1}</span>
               <span class='track-title'>${escapeHtml(t.title || "")}</span>
               ${artistLink(t.artist || t.album_artist)}
               <span class='track-dur hint'>${fmtDur(t.duration)}</span>
               <button class='row-mini' data-act='up' data-i='${i}' title='Subir'><i class="ph-bold ph-caret-up"></i></button>
               <button class='row-mini' data-act='down' data-i='${i}' title='Bajar'><i class="ph-bold ph-caret-down"></i></button>
               <button class='row-mini' data-act='del' data-tid='${t.id}' title='Quitar'><i class="ph-bold ph-x"></i></button>
             </div>`
        )
        .join("")
    : "<div class='hint'>Vacía. Añade canciones con el botón ＋ en cualquier vista.</div>";

  markPlaying();
  $("#pl-tracks").querySelectorAll(".track-row").forEach((row) => {
    row.addEventListener("dblclick", () =>
      window.pywebview.api.play_playlist(pid, parseInt(row.dataset.i))
    );
  });
  $("#pl-tracks").querySelectorAll(".row-mini").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const act = btn.dataset.act;
      if (act === "del") await window.pywebview.api.playlist_remove(pid, parseInt(btn.dataset.tid));
      else await window.pywebview.api.playlist_move(pid, parseInt(btn.dataset.i), act === "up" ? -1 : 1);
      openPlaylist(pid, CURRENT_PLAYLIST.name);
    });
  });
}

// --- Orden y filtro (compartidos por Álbumes y Canciones) -------------------

function libSort() {
  const el = $("#lib-sort");
  return el ? el.value : "artist";
}

function onlyLossless() {
  const el = $("#lib-lossless");
  return !!(el && el.checked);
}

const _txt = (v) => String(v || "").toLowerCase();
const _num = (v) => (typeof v === "number" ? v : parseFloat(v) || 0);

// Comparador para ÁLBUMES según el selector de orden.
function sortAlbums(list) {
  const mode = libSort();
  const by = {
    artist: (a, b) => _txt(a.album_artist).localeCompare(_txt(b.album_artist)) ||
                      _txt(a.album).localeCompare(_txt(b.album)),
    title: (a, b) => _txt(a.album).localeCompare(_txt(b.album)),
    year: (a, b) => _txt(b.date).localeCompare(_txt(a.date)),
    added: (a, b) => _num(b.added_at) - _num(a.added_at),
    plays: (a, b) => _num(b.play_count) - _num(a.play_count),
    duration: (a, b) => _num(b.total_duration) - _num(a.total_duration),
  };
  return list.slice().sort(by[mode] || by.artist);
}

// Comparador para PISTAS.
function sortTracks(list) {
  const mode = libSort();
  const by = {
    artist: (a, b) => _txt(a.album_artist || a.artist).localeCompare(_txt(b.album_artist || b.artist)) ||
                      _txt(a.album).localeCompare(_txt(b.album)) ||
                      _num(a.track_number) - _num(b.track_number),
    title: (a, b) => _txt(a.title).localeCompare(_txt(b.title)),
    year: (a, b) => _txt(b.date).localeCompare(_txt(a.date)),
    added: (a, b) => _num(b.added_at) - _num(a.added_at),
    plays: (a, b) => _num(b.play_count) - _num(a.play_count),
    duration: (a, b) => _num(b.duration) - _num(a.duration),
  };
  return list.slice().sort(by[mode] || by.artist);
}

function wireLibTools() {
  const rerender = () => refreshLibrary();
  $("#lib-sort").addEventListener("change", rerender);
  $("#lib-lossless").addEventListener("change", rerender);
}

async function loadAlbums(resetPage = true) {
  closeAlbumPage();   // volver al grid al (re)cargar la subvista
  let albums = await window.pywebview.api.list_albums();
  const stats = await window.pywebview.api.library_stats();
  $("#lib-stats").textContent = `${stats.tracks} pistas · ${stats.albums} álbumes · ${stats.artists} artistas`;
  if (onlyLossless()) albums = albums.filter((a) => a.all_lossless);
  ALBUMS = sortAlbums(albums);
  ALBUMS_TOTAL_TRACKS = stats.tracks;
  if (resetPage) ALBUMS_PAGE = 0;
  applyAlbumsFilter(false);
}

let ALBUMS_TOTAL_TRACKS = 0;

function applyAlbumsFilter(resetPage = true) {
  const q = ($("#albums-filter").value || "").trim().toLowerCase();
  ALBUMS_VIEW = !q
    ? ALBUMS
    : ALBUMS.filter((a) =>
        [a.album, a.album_artist].some((v) => (v || "").toLowerCase().includes(q))
      );
  if (resetPage) ALBUMS_PAGE = 0;
  renderAlbumsPage();
}

function renderAlbumsPage() {
  const grid = $("#albums");
  const pages = Math.max(1, Math.ceil(ALBUMS_VIEW.length / GRID_PER_PAGE));
  ALBUMS_PAGE = Math.max(0, Math.min(ALBUMS_PAGE, pages - 1));
  const from = ALBUMS_PAGE * GRID_PER_PAGE;
  const slice = ALBUMS_VIEW.slice(from, from + GRID_PER_PAGE);

  releaseCovers(grid);
  grid.innerHTML = "";
  if (!ALBUMS.length) {
    grid.innerHTML = emptyState(ALBUMS_TOTAL_TRACKS);
    wireEmptyState();
    $("#albums-pager").hidden = true;
    return;
  }
  if (!slice.length) {
    grid.innerHTML = `<div class='hint empty'>Ningún álbum coincide con el filtro.</div>`;
    $("#albums-pager").hidden = true;
    return;
  }
  for (const alb of slice) {
    const card = document.createElement("div");
    card.className = "album-card";
    card.innerHTML = `
      <div class="album-cover" data-cover="${escapeHtml(alb.cover_path || "")}"></div>
      <div class="album-name">${escapeHtml(alb.album || "(sin álbum)")}${
        alb.all_lossless ? '<span class="badge-lossless">LOSSLESS</span>' : ""
      }</div>
      <div class="album-artist hint">${artistLink(alb.album_artist)}</div>`;
    card.addEventListener("click", () => openAlbum(alb.album));
    grid.appendChild(card);
    lazyCover(card.querySelector(".album-cover"), alb.cover_path);
  }
  const pager = $("#albums-pager");
  pager.hidden = ALBUMS_VIEW.length <= GRID_PER_PAGE;
  $("#albums-page-info").textContent =
    `Página ${ALBUMS_PAGE + 1} / ${pages} · ${from + 1}–${Math.min(from + GRID_PER_PAGE, ALBUMS_VIEW.length)} de ${ALBUMS_VIEW.length}`;
  $("#albums-prev").disabled = ALBUMS_PAGE === 0;
  $("#albums-next").disabled = ALBUMS_PAGE >= pages - 1;
}

// Estado vacío: distingue "biblioteca vacía" de "el filtro no deja nada".
function emptyState(totalTracks) {
  if (!totalTracks) {
    return `<div class="empty-state">
      <i class="ph-bold ph-music-notes-plus"></i>
      <h4>Tu biblioteca está vacía</h4>
      <p>Importa música que ya tengas o descarga algo para empezar.</p>
      <div class="actions">
        <button class="primary" data-go="importar"><i class="ph-bold ph-folder-plus"></i>Importar música</button>
        <button class="secondary" data-go="descargas"><i class="ph-bold ph-download-simple"></i>Descargar</button>
      </div></div>`;
  }
  return `<div class="empty-state">
    <i class="ph-bold ph-funnel"></i>
    <h4>Nada con este filtro</h4>
    <p>Ningún álbum es completamente lossless. Prueba a desactivar «Solo lossless».</p>
    </div>`;
}

function wireEmptyState() {
  document.querySelectorAll(".empty-state [data-go]").forEach((b) =>
    b.addEventListener("click", () => switchView(b.dataset.go))
  );
}

// --- Artistas ---
async function loadArtists(resetPage = true) {
  const artists = await window.pywebview.api.list_artists();
  $("#artist-page").hidden = true;
  $("#artists").hidden = false;
  ARTISTS = artists;
  if (resetPage) ARTISTS_PAGE = 0;
  applyArtistsFilter(false);
}

function applyArtistsFilter(resetPage = true) {
  const q = ($("#artists-filter").value || "").trim().toLowerCase();
  ARTISTS_VIEW = !q
    ? ARTISTS
    : ARTISTS.filter((a) => (a.name || "").toLowerCase().includes(q));
  if (resetPage) ARTISTS_PAGE = 0;
  renderArtistsPage();
}

function renderArtistsPage() {
  const grid = $("#artists");
  const pages = Math.max(1, Math.ceil(ARTISTS_VIEW.length / GRID_PER_PAGE));
  ARTISTS_PAGE = Math.max(0, Math.min(ARTISTS_PAGE, pages - 1));
  const from = ARTISTS_PAGE * GRID_PER_PAGE;
  const slice = ARTISTS_VIEW.slice(from, from + GRID_PER_PAGE);

  releaseCovers(grid);
  grid.innerHTML = "";
  if (!slice.length) {
    grid.innerHTML = `<div class='hint empty'>Ningún artista coincide con el filtro.</div>`;
    $("#artists-pager").hidden = true;
    return;
  }
  for (const a of slice) {
    const card = document.createElement("div");
    card.className = "album-card";
    card.innerHTML = `
      <div class="album-cover artist-cover"></div>
      <div class="album-name">${escapeHtml(a.name || "(sin artista)")}</div>
      <div class="album-artist hint">${a.album_count} álbum(es) · ${a.track_count} pista(s)</div>`;
    card.addEventListener("click", () => openArtist(a.name));
    grid.appendChild(card);
    lazyCover(card.querySelector(".album-cover"), a.cover_path);
  }
  const pager = $("#artists-pager");
  pager.hidden = ARTISTS_VIEW.length <= GRID_PER_PAGE;
  $("#artists-page-info").textContent =
    `Página ${ARTISTS_PAGE + 1} / ${pages} · ${from + 1}–${Math.min(from + GRID_PER_PAGE, ARTISTS_VIEW.length)} de ${ARTISTS_VIEW.length}`;
  $("#artists-prev").disabled = ARTISTS_PAGE === 0;
  $("#artists-next").disabled = ARTISTS_PAGE >= pages - 1;
}

async function openArtist(name) {
  // Navegar a Biblioteca > Artistas y abrir la página del artista.
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.querySelector('.tab[data-view="biblioteca"]').classList.add("active");
  $("#view-biblioteca").classList.add("active");
  CURRENT_SUB = "artists";
  document.querySelectorAll(".subtab").forEach((t) =>
    t.classList.toggle("active", t.dataset.sub === "artists")
  );
  document.querySelectorAll(".subview").forEach((v) => v.classList.remove("active"));
  $("#sub-artists").classList.add("active");

  const page = await window.pywebview.api.artist_page(name);
  $("#artists").hidden = true;
  $("#artist-page").hidden = false;
  $("#artist-name").textContent = page.name || "(sin artista)";
  $("#artist-meta").textContent =
    `${page.albums.length} álbum(es) · ${page.track_count} pista(s) · ${fmtDur(page.total_duration)}`;
  $("#btn-artist-play").onclick = () => window.pywebview.api.play_artist(name, false);
  $("#btn-artist-shuffle").onclick = () => window.pywebview.api.play_artist(name, true);
  $("#btn-artist-playlist").onclick = (e) =>
    openPlaylistMenuForMany(e.currentTarget, page.tracks.map((t) => t.id), name || "");

  const albums = $("#artist-albums");
  albums.innerHTML = "";
  for (const alb of page.albums) {
    const card = document.createElement("div");
    card.className = "album-card";
    card.innerHTML = `
      <div class="album-cover"></div>
      <div class="album-name">${escapeHtml(alb.album || "(sin álbum)")}</div>
      <div class="album-artist hint">${alb.track_count} pista(s)</div>`;
    card.addEventListener("click", () => openAlbum(alb.album));
    albums.appendChild(card);
    lazyCover(card.querySelector(".album-cover"), alb.cover_path);
  }

  CURRENT_ARTIST_TRACKS = page.tracks;
  $("#artist-tracks").innerHTML = page.tracks
    .map(
      (t, i) =>
        `<div class='track-row' data-i='${i}' data-track-id='${t.id}'>
           <span class='track-num'>${i + 1}</span>
           <span class='track-title'>${escapeHtml(t.title || "")}</span>
           <span class='hint'>${escapeHtml(t.album || "")}</span>
           <span class='track-dur hint'>${fmtDur(t.duration)}</span>
           ${plusBtn(t.id)}
         </div>`
    )
    .join("");
  markPlaying();
  $("#artist-tracks").querySelectorAll(".track-row").forEach((row) => {
    row.addEventListener("dblclick", () =>
      window.pywebview.api.play_artist_from(name, parseInt(row.dataset.i))
    );
  });
}

// --- Canciones (todas) ---
async function loadSongs() {
  let tracks = await window.pywebview.api.all_tracks();
  if (onlyLossless()) tracks = tracks.filter((t) => t.lossless);
  SONGS = sortTracks(tracks);
  SONGS_PAGE = 0;
  applySongsFilter();
}

// data-i es el índice dentro de SONGS_VIEW (lo que se ve tras filtrar y ordenar).
// Al reproducir se manda esa lista de ids EXPLÍCITA con play_ids(), así que
// filtrar o paginar no puede desalinear nada.
function songRow(t, i) {
  return `<div class='track-row' data-i='${i}' data-track-id='${t.id}'>
            <span class='track-num'>${i + 1}</span>
            <span class='track-title'>${escapeHtml(t.title || "")}</span>
            ${artistLink(t.artist || t.album_artist)}
            <span class='hint'>${escapeHtml(t.album || "")}</span>
            <span class='track-dur hint'>${fmtDur(t.duration)}</span>
            ${plusBtn(t.id)}
          </div>`;
}

// Filtro local e instantáneo: no consulta al backend, acota el array ya cargado.
function applySongsFilter() {
  const q = ($("#songs-filter").value || "").trim().toLowerCase();
  SONGS_VIEW = !q
    ? SONGS
    : SONGS.filter((t) =>
        [t.title, t.artist, t.album_artist, t.album]
          .some((v) => (v || "").toLowerCase().includes(q))
      );
  SONGS_PAGE = 0;          // un filtro nuevo siempre empieza por la página 1
  renderSongsPage();
}

function songsPageCount() {
  return Math.max(1, Math.ceil(SONGS_VIEW.length / SONGS_PER_PAGE));
}

function renderSongsPage() {
  const pages = songsPageCount();
  SONGS_PAGE = Math.max(0, Math.min(SONGS_PAGE, pages - 1));
  const from = SONGS_PAGE * SONGS_PER_PAGE;
  const slice = SONGS_VIEW.slice(from, from + SONGS_PER_PAGE);

  $("#songs").innerHTML = slice.length
    ? slice.map((t, k) => songRow(t, from + k)).join("")
    : `<div class='hint empty'>Ninguna canción coincide con el filtro.</div>`;

  const total = SONGS.length;
  const shown = SONGS_VIEW.length;
  $("#songs-head").textContent = shown === total
    ? `${total} pista(s) — doble clic para reproducir desde ahí`
    : `${shown} de ${total} pistas — doble clic para reproducir desde ahí`;

  const pager = $("#songs-pager");
  pager.hidden = shown <= SONGS_PER_PAGE;
  $("#songs-page-info").textContent =
    `Página ${SONGS_PAGE + 1} / ${pages} · ${from + 1}–${Math.min(from + SONGS_PER_PAGE, shown)}`;
  $("#songs-prev").disabled = SONGS_PAGE === 0;
  $("#songs-next").disabled = SONGS_PAGE >= pages - 1;
  markPlaying();           // la página nueva puede contener la pista en curso
}

function goSongsPage(delta) {
  SONGS_PAGE += delta;
  renderSongsPage();
  $("#sub-songs").scrollIntoView({ block: "start", behavior: "smooth" });
}

// Portadas DIFERIDAS de verdad. Antes se pedía cover_uri() de todas las tarjetas
// nada más pintar el grid, y cada llamada lee el fichero y lo pasa en base64 por
// el puente: con una biblioteca grande son decenas de lecturas simultáneas para
// portadas que ni se ven. Ahora solo se piden las que entran en pantalla.
let COVER_OBS = null;

function coverObserver() {
  if (COVER_OBS) return COVER_OBS;
  COVER_OBS = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        COVER_OBS.unobserve(e.target);      // una sola vez por tarjeta
        fetchCover(e.target, e.target.dataset.cover);
      }
    },
    { rootMargin: "200px" }
  );
  return COVER_OBS;
}

// El observer es PERSISTENTE: nunca se desconecta por completo.
//
// Antes hacía disconnect() global, y eso provocaba un fallo real: al arrancar,
// «Para ti» y Álbumes cargan a la vez (ambas son async y no se esperan), así que
// la segunda en terminar CANCELABA las observaciones pendientes de la primera y
// esas portadas no se pedían nunca — de ahí que aparecieran al ir a otra pantalla
// y volver, cuando solo carga una vista. Ahora se sueltan únicamente los nodos
// del contenedor que se va a repintar, que es lo único que hace falta para no
// acumular elementos muertos.
function releaseCovers(container) {
  if (!COVER_OBS || !container) return;
  container.querySelectorAll("[data-cover]").forEach((el) => COVER_OBS.unobserve(el));
}

function lazyCover(el, coverPath) {
  if (!el || !coverPath) return;
  el.dataset.cover = coverPath;
  coverObserver().observe(el);
}

async function fetchCover(el, coverPath) {
  if (!coverPath) return;
  const uri = await window.pywebview.api.cover_uri(coverPath);
  if (uri) el.style.backgroundImage = `url(${uri})`;
}

function closeAlbumPage() {
  $("#album-page").hidden = true;
  $("#albums").hidden = false;
}

async function openAlbum(album) {
  CURRENT_ALBUM = album;
  // Asegurar que estamos en Biblioteca › Álbumes (puede llamarse desde el
  // reproductor, la búsqueda o la página de artista).
  switchView("biblioteca");
  if (CURRENT_SUB !== "albums") switchSub("albums", false);

  const tracks = await window.pywebview.api.album_tracks(album);

  // Ficha derivada de las pistas: duración total, año, calidad.
  const totalDur = tracks.reduce((s, t) => s + (t.duration || 0), 0);
  const year = tracks.map((t) => t.date).find(Boolean) || "";
  const allLossless = tracks.length > 0 && tracks.every((t) => t.lossless);
  const artist = tracks.map((t) => t.album_artist).find(Boolean) ||
    (new Set(tracks.map((t) => t.artist).filter(Boolean)).size > 1
      ? "Varios artistas"
      : tracks.map((t) => t.artist).find(Boolean) || "");
  const meta = [
    `${tracks.length} pista(s)`,
    fmtDur(totalDur),
    year,
    allLossless ? "Lossless" : "",
  ].filter(Boolean).join(" · ");

  $("#albums").hidden = true;
  $("#album-page").hidden = false;
  $("#album-page-title").textContent = album || "(sin álbum)";
  $("#album-page-artist").innerHTML = artist ? artistLink(artist) : "";
  $("#album-page-meta").innerHTML = meta +
    (allLossless ? ' <span class="badge-lossless">LOSSLESS</span>' : "");

  const coverEl = $("#album-page-cover");
  coverEl.style.backgroundImage = "";
  const coverPath = tracks.map((t) => t.cover_path).find(Boolean);
  if (coverPath) fetchCover(coverEl, coverPath);

  $("#album-tracks").innerHTML = tracks
    .map(
      (t, i) =>
        `<div class="track-row" data-i="${i}" data-track-id="${t.id}">
           <span class="track-num">${t.track_number || i + 1}</span>
           <span class="track-title">${escapeHtml(t.title || "")}</span>
           <span class="track-dur hint">${fmtDur(t.duration)}</span>
           ${plusBtn(t.id)}
         </div>`
    )
    .join("");
  markPlaying();
  window.scrollTo(0, 0);

  $("#btn-play-album").onclick = () => window.pywebview.api.play_album(album);
  $("#btn-album-playlist").onclick = (e) =>
    openPlaylistMenuForMany(e.currentTarget, tracks.map((t) => t.id), album || "");
  $("#btn-shuffle-album").onclick = async () => {
    const ids = tracks.map((t) => t.id);
    // Reproduce el álbum en orden aleatorio (mismo criterio que artista).
    await window.pywebview.api.play_ids(shuffled(ids), 0);
  };
  $("#btn-del-album").onclick = async () => {
    const ok = await confirmDialog({
      title: "Eliminar álbum",
      msg: `Se enviarán a la Papelera de reciclaje las ${tracks.length} pista(s) de «${album || "(sin álbum)"}» y se quitarán de la biblioteca. ¿Continuar?`,
    });
    if (!ok) return;
    const res = await window.pywebview.api.delete_album(album);
    closeAlbumPage();
    toast(res.ok ? `Álbum eliminado (${res.count} pista(s) a la Papelera).` : "No se pudo eliminar.", !res.ok);
    refreshLibrary();
  };
  $("#album-tracks").querySelectorAll(".track-row").forEach((row) => {
    row.addEventListener("dblclick", () =>
      window.pywebview.api.play_album_from(album, parseInt(row.dataset.i))
    );
  });
}

function shuffled(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// --- Búsqueda ---------------------------------------------------------------
// Los resultados van a su propio contenedor y las subvistas se ocultan con la
// clase .searching, en vez de inyectar un panel dentro del grid de álbumes.

function exitSearch() {
  $("#view-biblioteca").classList.remove("searching");
  $("#search-results").hidden = true;
}

async function doSearch(query) {
  query = (query || "").trim();
  if (!query) {
    exitSearch();
    return refreshLibrary();
  }
  const results = await window.pywebview.api.search_library(query);
  const box = $("#search-results");
  $("#view-biblioteca").classList.add("searching");
  box.hidden = false;

  const head =
    `<div class="row inline search-head">
       <strong>${results.length} resultado(s)</strong>
       <span class="hint">para «${escapeHtml(query)}»</span>
       <button id="btn-search-clear" class="secondary">Limpiar</button>
     </div>`;

  if (!results.length) {
    box.innerHTML = head +
      `<div class="hint empty">Nada coincide. Prueba con menos palabras.</div>`;
    $("#btn-search-clear").addEventListener("click", clearSearch);
    return;
  }

  // Álbumes y artistas se derivan de las pistas encontradas (sin más consultas).
  const albums = [];
  const seenAlb = new Set();
  const artists = [];
  const seenArt = new Set();
  for (const t of results) {
    const alb = t.album || "";
    if (alb && !seenAlb.has(alb)) {
      seenAlb.add(alb);
      albums.push({ album: t.album, artist: t.album_artist || t.artist });
    }
    const art = t.album_artist || t.artist || "";
    if (art && !seenArt.has(art)) {
      seenArt.add(art);
      artists.push(art);
    }
  }

  let html = head;
  if (albums.length) {
    html += `<div class="search-group"><div class="search-label">Álbumes</div>` +
      albums.map((a) =>
        `<div class="track-row search-alb" data-album="${escapeHtml(a.album)}">
           <i class="ph-bold ph-vinyl-record"></i>
           <span class="track-title">${escapeHtml(a.album)}</span>
           <span class="hint">${escapeHtml(a.artist || "")}</span>
         </div>`).join("") + `</div>`;
  }
  if (artists.length) {
    html += `<div class="search-group"><div class="search-label">Artistas</div>` +
      artists.map((a) =>
        `<div class="track-row">
           <i class="ph-bold ph-user"></i>
           <span class="track-title">${artistLink(a)}</span>
         </div>`).join("") + `</div>`;
  }
  html += `<div class="search-group"><div class="search-label">Canciones</div>` +
    results.map((t) =>
      `<div class="track-row" data-track-id="${t.id}">
         <span class="track-title">${escapeHtml(t.title || "")}</span>
         ${artistLink(t.artist || t.album_artist)}
         <span class="hint">${escapeHtml(t.album || "")}</span>
         <span class="track-dur hint">${fmtDur(t.duration)}</span>
         ${plusBtn(t.id)}
       </div>`).join("") + `</div>`;

  box.innerHTML = html;
  markPlaying();
  $("#btn-search-clear").addEventListener("click", clearSearch);
  box.querySelectorAll(".search-alb").forEach((row) => {
    row.addEventListener("click", () => {
      $("#search").value = "";
      exitSearch();                // openAlbum ya navega a Biblioteca › Álbumes
      openAlbum(row.dataset.album);
    });
  });
}

function clearSearch() {
  $("#search").value = "";
  exitSearch();
  refreshLibrary();
}

// ============ PARA TI ============
// Las mezclas se calculan en el backend a partir de la propia biblioteca (género,
// época, escuchas). Aquí solo se pintan y se conectan a reproducir / guardar.

let FY_MIXES = [];

async function loadForYou() {
  const data = await window.pywebview.api.for_you();
  FY_MIXES = data.mixes || [];
  const st = data.stats || {};

  $("#fy-hero-num").textContent = st.plays ? String(st.plays) : "";
  const bits = [];
  if (st.tracks) bits.push(`${st.tracks} pistas`);
  if (st.albums) bits.push(`${st.albums} álbumes`);
  if (st.plays) bits.push(`${st.plays} reproducciones`);
  $("#fy-stats").innerHTML =
    bits.map((b) => `<span class="fy-stat">${escapeHtml(b)}</span>`).join("") +
    (st.top ? `<span class="fy-stat fy-top">Tu #1: ${escapeHtml(st.top.title || "")}
       <em>${escapeHtml(st.top.artist || "")}</em> · ${st.top.play_count}×</span>` : "");

  // Soltar SOLO los nodos de esta pantalla (nunca el observer entero: otras
  // vistas pueden estar cargando sus portadas al mismo tiempo).
  ["#fy-strip-artists", "#fy-strip-albums", "#fy-strip-added", "#fy-grid"]
    .forEach((sel) => releaseCovers($(sel)));
  renderStrips(data.strips || {});

  const grid = $("#fy-grid");
  const empty = $("#fy-empty");
  empty.hidden = FY_MIXES.length > 0;
  empty.querySelectorAll("[data-go]").forEach((b) => {
    b.onclick = () => switchView(b.dataset.go);
  });
  $("#fy-mixes-title").hidden = FY_MIXES.length === 0;
  grid.innerHTML = FY_MIXES.map(fyCard).join("");

  // Portadas diferidas, igual que en el resto de la app (el observer ya se
  // reinició arriba, antes de pintar las tiras).
  grid.querySelectorAll(".fy-cover").forEach((el) => {
    if (el.dataset.cover) lazyCover(el, el.dataset.cover);
  });

  grid.querySelectorAll(".fy-card").forEach((card) => {
    const mix = FY_MIXES.find((m) => m.key === card.dataset.key);
    if (!mix) return;
    card.querySelector(".fy-play").addEventListener("click", async (e) => {
      e.stopPropagation();
      const res = await window.pywebview.api.play_ids(mix.track_ids, 0);
      if (res && res.ok) toast(`Sonando: ${mix.title}`);
    });
    card.querySelector(".fy-save").addEventListener("click", (e) => {
      e.stopPropagation();
      openPlaylistMenuForMany(e.currentTarget, mix.track_ids, mix.title);
    });
    card.addEventListener("click", () => window.pywebview.api.play_ids(mix.track_ids, 0));
  });
}

// --- Tiras de recientes ------------------------------------------------------
// Cada tipo tiene su propia forma: artistas en retratos circulares, álbumes en
// estantería inclinada. Así la pantalla respira en vez de ser una rejilla uniforme.

function renderStrips(strips) {
  renderArtistRail($("#fy-strip-artists"), strips.artists || []);
  renderShelf($("#fy-strip-albums"), strips.albums || [], "albums");
  renderShelf($("#fy-strip-added"), strips.added || [], "added");
}

function renderArtistRail(section, artists) {
  section.hidden = artists.length === 0;
  if (!artists.length) return;
  const rail = section.querySelector(".fy-rail");
  rail.innerHTML = artists
    .map(
      (a, i) => `<button class="fy-artist" data-artist="${escapeHtml(a.name || "")}" style="--i:${i}">
          <span class="fy-portrait"><span class="fy-portrait-img" data-cover="${escapeHtml(a.cover_path || "")}"></span></span>
          <span class="fy-artist-name">${escapeHtml(a.name || "(sin artista)")}</span>
          <span class="fy-artist-meta">${a.plays}× · ${a.tracks} pistas</span>
        </button>`
    )
    .join("");
  rail.querySelectorAll(".fy-artist").forEach((el) => {
    lazyCover(el.querySelector(".fy-portrait-img"), el.querySelector(".fy-portrait-img").dataset.cover);
    el.addEventListener("click", () => openArtist(el.dataset.artist || null));
  });
}

function renderShelf(section, albums, kind) {
  section.hidden = albums.length === 0;
  if (!albums.length) return;
  const rail = section.querySelector(".fy-rail");
  rail.innerHTML = albums
    .map(
      (a, i) => `<article class="fy-disc" data-album="${escapeHtml(a.album || "")}" style="--i:${i}">
          <span class="fy-disc-art" data-cover="${escapeHtml(a.cover_path || "")}">
            <button class="fy-disc-play" title="Reproducir álbum"><i class="ph-bold ph-play"></i></button>
          </span>
          <span class="fy-disc-name">${escapeHtml(a.album || "(sin álbum)")}</span>
          <span class="fy-disc-meta">${escapeHtml(a.artist || "")} · ${a.tracks} pistas</span>
        </article>`
    )
    .join("");
  rail.querySelectorAll(".fy-disc").forEach((el) => {
    const art = el.querySelector(".fy-disc-art");
    lazyCover(art, art.dataset.cover);
    el.addEventListener("click", () => openAlbum(el.dataset.album || null));
    el.querySelector(".fy-disc-play").addEventListener("click", (e) => {
      e.stopPropagation();
      window.pywebview.api.play_album(el.dataset.album || null);
      toast(`Sonando: ${el.dataset.album}`);
    });
  });
}

// Tarjeta con el lenguaje Tanatos: corte diagonal, tipografía display recortada y
// color alterno voltage/crimson. La primera ocupa el doble (rejilla asimétrica).
function fyCard(m, i) {
  const tone = i % 3 === 1 ? "crim" : "volt";
  const big = i === 0 ? " fy-big" : "";
  return `<article class="fy-card fy-${tone}${big}" data-key="${escapeHtml(m.key)}"
            style="--i:${i}">
      <div class="fy-cover" data-cover="${escapeHtml(m.cover_path || "")}"></div>
      <div class="fy-shade"></div>
      <div class="fy-body">
        <div class="fy-kind">${fyKindLabel(m.kind)}</div>
        <h3 class="fy-name">${escapeHtml(m.title)}</h3>
        <div class="fy-sub">${escapeHtml(m.subtitle || "")}</div>
      </div>
      <div class="fy-foot">
        <span class="fy-count">${m.count}<small>${m.total > m.count ? " / " + m.total : ""} pistas</small></span>
        <span class="fy-actions">
          <button class="fy-play" title="Reproducir la mezcla"><i class="ph-bold ph-play"></i></button>
          <button class="fy-save" title="Guardar como playlist"><i class="ph-bold ph-plus"></i></button>
        </span>
      </div>
    </article>`;
}

function fyKindLabel(kind) {
  return {
    rotation: "En rotación",
    genre: "Tu mezcla",
    fresh: "Rescate",
    decade: "Cápsula del tiempo",
    revisit: "Vuelve a…",
  }[kind] || "Mezcla";
}

// ============ REPRODUCTOR ============
let NP_DURATION = 0;
let seeking = false;

function wirePlayer() {
  $("#np-play").addEventListener("click", () => window.pywebview.api.player_toggle());
  $("#np-next").addEventListener("click", () => window.pywebview.api.player_next());
  $("#np-prev").addEventListener("click", () => window.pywebview.api.player_prev());
  $("#np-volume").addEventListener("input", (e) =>
    window.pywebview.api.player_volume(e.target.value)
  );
  // Nivelación: modo y preamp (resuelven el "suena bajo").
  $("#rg-mode").addEventListener("change", (e) =>
    window.pywebview.api.set_replaygain(e.target.value, null)
  );
  $("#rg-preamp").addEventListener("input", (e) => {
    $("#rg-preamp-val").textContent = "+" + e.target.value + " dB";
  });
  $("#rg-preamp").addEventListener("change", (e) =>
    window.pywebview.api.set_replaygain(null, parseFloat(e.target.value))
  );
  $("#np-seek").addEventListener("mousedown", () => (seeking = true));
  $("#np-seek").addEventListener("change", (e) => {
    seeking = false;
    window.pywebview.api.player_seek((e.target.value / 100) * NP_DURATION);
  });

  let shuffleOn = false;
  $("#np-shuffle").addEventListener("click", () => {
    shuffleOn = !shuffleOn;
    $("#np-shuffle").classList.toggle("on", shuffleOn);
    window.pywebview.api.player_shuffle(shuffleOn);
  });
  const repeatModes = ["off", "all", "one"];
  let ri = 0;
  $("#np-repeat").addEventListener("click", () => {
    ri = (ri + 1) % 3;
    $("#np-repeat").classList.toggle("on", ri !== 0);
    $("#np-repeat").innerHTML = ri === 2 ? '<i class="ph-bold ph-repeat-once"></i>' : '<i class="ph-bold ph-repeat"></i>';
    window.pywebview.api.player_repeat(repeatModes[ri]);
  });

  window.hub.on("player:track", async (p) => {
    const t = p.track;
    CURRENT_TRACK = t;
    $("#np-title").textContent = t.title || "—";
    $("#np-artist").innerHTML = t.artist ? artistLink(t.artist) : "";
    $("#np-album").textContent = t.album || "";
    // Ficha técnica: codec, sample rate, profundidad, bitrate, lossless.
    const specs = [];
    if (t.codec) specs.push(t.codec.toUpperCase());
    if (t.sample_rate) specs.push((t.sample_rate / 1000) + " kHz");
    if (t.bit_depth) specs.push(t.bit_depth + "-bit");
    if (t.bit_rate) specs.push(Math.round(t.bit_rate / 1000) + " kbps");
    specs.push(t.lossless ? "lossless" : "lossy");
    $("#np-specs").textContent = specs.join(" · ");
    const uri = t.cover_path ? await window.pywebview.api.cover_uri(t.cover_path) : null;
    $("#np-cover").src = uri || "";
    // Barra global + vecinos + resaltado en las listas.
    showMini(t, uri);
    renderNeighbours(p);
    markPlaying();
    refreshOpenQueue();   // la Cola (flotante o integrada) sigue el cambio de pista
    refreshSignalPath();
    if (!$("#lyrics-panel").hidden) loadLyrics();
    $("#spectrum-panel").hidden = true;
  });
  window.hub.on("player:position", (p) => {
    if (typeof p.duration === "number") {
      NP_DURATION = p.duration;
      $("#np-dur").textContent = fmtDur(p.duration);
      $("#mini-dur").textContent = fmtDur(p.duration);
    }
    if (typeof p.position === "number" && !seeking) {
      LAST_POS = p.position;                     // base para los atajos ←/→
      $("#np-cur").textContent = fmtDur(p.position);
      $("#mini-cur").textContent = fmtDur(p.position);
      // El indicador de la derecha muestra total o restante (se alterna al pulsar).
      if (SHOW_REMAINING && NP_DURATION) {
        const rest = Math.max(0, NP_DURATION - p.position);
        $("#np-dur").textContent = "-" + fmtDur(rest);
        $("#mini-dur").textContent = "-" + fmtDur(rest);
      }
      const pct = NP_DURATION ? (p.position / NP_DURATION) * 100 : 0;
      $("#np-seek").value = pct;
      $("#mini-fill").style.width = pct + "%";
      highlightLyric(p.position);
    }
  });
  window.hub.on("player:state", (p) => renderPlayState(!!p.playing));
  window.hub.on("player:neighbours", renderNeighbours);
  window.hub.on("player:signalpath", renderSignalPath);
  // El dispositivo guardado ya no existe (típico tras una actualización de
  // Windows, que regenera los ids, o con un DAC USB desconectado).
  window.hub.on("player:device-missing", (p) => {
    toast("El dispositivo de audio elegido ya no está disponible. Se usa el " +
          "predeterminado del sistema; puedes elegir otro en Ajustes.", true);
  });

  wireMini();
  wireAudiophile();
}

// --- Estado compartido entre la vista Reproduciendo y la barra global --------
// Un único renderizador por cosa: si cada sitio pintara por su cuenta, los dos
// juegos de controles se desincronizarían (le pasaba ya a #np-play).

let PLAYING = false;

function renderPlayState(playing) {
  PLAYING = playing;
  const icon = playing
    ? '<i class="ph-bold ph-pause"></i>'
    : '<i class="ph-bold ph-play"></i>';
  $("#np-play").innerHTML = icon;
  $("#mini-play").innerHTML = icon;
  document.querySelectorAll(".track-row.playing").forEach((r) =>
    r.classList.toggle("paused", !playing)
  );
}

let HAS_TRACK = false;

function showMini(t, coverUri) {
  HAS_TRACK = true;
  $("#mini-title").textContent = t.title || "—";
  $("#mini-artist").textContent = [t.artist, t.album].filter(Boolean).join(" — ");
  $("#mini-cover").src = coverUri || "";
  updateMiniVisibility();
}

// La barra global sobra en la vista Reproduciendo (duplica esos controles), así
// que se muestra solo cuando hay pista Y la vista activa NO es el reproductor.
function updateMiniVisibility() {
  const active = document.querySelector(".view.active");
  const inPlayer = active && active.id === "view-reproduciendo";
  const show = HAS_TRACK && !inPlayer;
  $("#miniplayer").hidden = !show;
  document.body.classList.toggle("has-mini", show);
  if (!show) toggleQueue(false);   // no dejar la cola flotante abierta sin barra
}

function renderNeighbours(p) {
  const set = (btn, data) => {
    if (!data || !data.title) {
      btn.hidden = true;
      return;
    }
    btn.hidden = false;
    btn.querySelector(".nb-title").textContent = data.title;
    btn.title = data.artist ? `${data.artist} — ${data.title}` : data.title;
  };
  set($("#np-prev-label"), p && p.prev);
  set($("#np-next-label"), p && p.next);
}

// Marca en CUALQUIER lista la fila de la pista que está sonando. Hay que llamarla
// también después de cada render: al repintar se pierde la clase.
function markPlaying() {
  const id = CURRENT_TRACK && CURRENT_TRACK.id != null ? String(CURRENT_TRACK.id) : null;
  // Limpiar la marca anterior restaurando el número de pista original: las barras
  // del ecualizador SUSTITUYEN ese contenido, así que hay que guardarlo antes.
  document.querySelectorAll(".track-row.playing").forEach((r) => {
    r.classList.remove("playing", "paused");
    const num = r.querySelector(".track-num");
    if (num && num.dataset.orig !== undefined) {
      num.textContent = num.dataset.orig;
      delete num.dataset.orig;
    }
  });
  if (id === null) return;
  document.querySelectorAll('.track-row[data-track-id="' + id + '"]').forEach((r) => {
    r.classList.add("playing");
    r.classList.toggle("paused", !PLAYING);
    const num = r.querySelector(".track-num");
    if (num && num.dataset.orig === undefined) {
      num.dataset.orig = num.textContent;
      num.innerHTML = '<span class="eqbars"><span></span><span></span><span></span></span>';
    }
  });
}

// --- Cola --------------------------------------------------------------------
// Dos presentaciones de la MISMA cola:
//  - En Reproduciendo: panel EN EL FLUJO (#queue-inline), como Letras/EQ. No tapa.
//  - En el resto de vistas: panel FLOTANTE (#queue-panel), superposición rápida.
// El contexto lo decide inQueueView().

function inQueueView() {
  const active = document.querySelector(".view.active");
  return active && active.id === "view-reproduciendo";
}

function queueTarget() {
  return inQueueView()
    ? { panel: $("#queue-inline"), list: $("#qi-list"), count: $("#qi-count") }
    : { panel: $("#queue-panel"), list: $("#q-list"), count: $("#q-count") };
}

async function toggleQueue(force) {
  // Cerrar cualquiera de los dos si se pide ocultar.
  if (force === false) {
    $("#queue-panel").hidden = true;
    $("#queue-inline").hidden = true;
    return;
  }
  const t = queueTarget();
  const show = force !== undefined ? force : t.panel.hidden;
  // Solo un panel de cola visible a la vez.
  $("#queue-panel").hidden = true;
  $("#queue-inline").hidden = true;
  if (!show) return;
  await renderQueueInto(t.list, t.count);
  t.panel.hidden = false;
}

// Refresca el panel de cola que esté abierto (al cambiar de pista).
function refreshOpenQueue() {
  if (!$("#queue-inline").hidden) renderQueueInto($("#qi-list"), $("#qi-count"));
  if (!$("#queue-panel").hidden) renderQueueInto($("#q-list"), $("#q-count"));
}

async function renderQueueInto(listEl, countEl) {
  const q = await window.pywebview.api.player_queue();
  const list = q.queue || [];
  const idx = typeof q.index === "number" ? q.index : -1;
  countEl.textContent = list.length ? `${idx + 1} de ${list.length}` : "vacía";
  listEl.innerHTML = list.length
    ? list
        .map(
          (t, i) =>
            `<div class="q-row ${i === idx ? "now" : i < idx ? "past" : ""}" data-qi="${i}">
               <span class="q-num">${i === idx ? '<i class="ph-bold ph-speaker-high"></i>' : i + 1}</span>
               <span class="q-meta">
                 <span class="q-title">${escapeHtml(t.title || "")}</span>
                 <span class="q-artist">${escapeHtml(t.artist || "")}</span>
               </span>
               <span class="q-dur">${fmtDur(t.duration)}</span>
             </div>`
        )
        .join("")
    : `<div class="hint empty">No hay nada en la cola. Reproduce un álbum o una playlist.</div>`;

  listEl.querySelectorAll(".q-row").forEach((row) => {
    row.addEventListener("click", async () => {
      await window.pywebview.api.player_play_index(parseInt(row.dataset.qi));
      setTimeout(refreshOpenQueue, 600);
    });
  });
  const now = listEl.querySelector(".q-row.now");
  if (now) now.scrollIntoView({ block: "nearest" });
}

// --- Atajos de teclado -------------------------------------------------------
// No deben dispararse mientras se escribe en un campo.

function isTyping(e) {
  const t = e.target;
  if (!t) return false;
  const tag = (t.tagName || "").toLowerCase();
  return tag === "input" || tag === "select" || tag === "textarea" || t.isContentEditable;
}

function wireShortcuts() {
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && (e.key === "f" || e.key === "F")) {
      e.preventDefault();
      switchView("biblioteca");
      $("#search").focus();
      $("#search").select();
      return;
    }
    if (isTyping(e) || e.altKey || e.metaKey) return;

    switch (e.key) {
      case " ":                                   // play / pausa
        e.preventDefault();
        window.pywebview.api.player_toggle();
        break;
      case "ArrowRight":
        e.preventDefault();
        if (e.ctrlKey) window.pywebview.api.player_next();
        else if (NP_DURATION) seekBy(5);
        break;
      case "ArrowLeft":
        e.preventDefault();
        if (e.ctrlKey) window.pywebview.api.player_prev();
        else if (NP_DURATION) seekBy(-5);
        break;
      case "/":
        e.preventDefault();
        switchView("biblioteca");
        $("#search").focus();
        break;
      case "q":
      case "Q":
        toggleQueue();
        break;
      default:
        break;
    }
  });
}

let LAST_POS = 0;

function seekBy(delta) {
  const target = Math.max(0, Math.min(NP_DURATION, LAST_POS + delta));
  window.pywebview.api.player_seek(target);
}

function wireMini() {
  $("#mini-play").addEventListener("click", () => window.pywebview.api.player_toggle());
  $("#mini-prev").addEventListener("click", () => window.pywebview.api.player_prev());
  $("#mini-next").addEventListener("click", () => window.pywebview.api.player_next());
  $("#mini-meta").addEventListener("click", () => switchView("reproduciendo"));
  // Volumen sincronizado en los dos sentidos.
  $("#mini-volume").addEventListener("input", (e) => {
    $("#np-volume").value = e.target.value;
    window.pywebview.api.player_volume(e.target.value);
  });
  $("#np-volume").addEventListener("input", (e) => {
    $("#mini-volume").value = e.target.value;
  });
  // Clic en la barra fina = buscar posición.
  $("#mini-bar").addEventListener("click", (e) => {
    if (!NP_DURATION) return;
    const r = $("#mini-bar").getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    window.pywebview.api.player_seek(frac * NP_DURATION);
  });
  // Etiquetas laterales: equivalen a los botones de anterior / siguiente.
  $("#np-prev-label").addEventListener("click", () => window.pywebview.api.player_prev());
  $("#np-next-label").addEventListener("click", () => window.pywebview.api.player_next());

  // Cola.
  $("#mini-queue").addEventListener("click", () => toggleQueue());
  $("#btn-q-close").addEventListener("click", () => toggleQueue(false));
  $("#btn-queue-inline").addEventListener("click", () => toggleQueue());

  // Rueda del ratón sobre cualquiera de los dos volúmenes.
  [$("#np-volume"), $("#mini-volume")].forEach((sl) => {
    sl.addEventListener("wheel", (e) => {
      e.preventDefault();
      const v = Math.max(0, Math.min(100, parseInt(sl.value) + (e.deltaY < 0 ? 3 : -3)));
      sl.value = v;
      $("#np-volume").value = v;
      $("#mini-volume").value = v;
      window.pywebview.api.player_volume(v);
    }, { passive: false });
  });

  // Clic en el tiempo de la derecha: alterna total / restante.
  const toggleRemaining = () => {
    SHOW_REMAINING = !SHOW_REMAINING;
    if (!SHOW_REMAINING) {
      $("#np-dur").textContent = fmtDur(NP_DURATION);
      $("#mini-dur").textContent = fmtDur(NP_DURATION);
    }
  };
  $("#np-dur").addEventListener("click", toggleRemaining);
  $("#mini-dur").addEventListener("click", toggleRemaining);
  $("#np-dur").style.cursor = "pointer";
  $("#np-dur").title = "Alternar tiempo total / restante";

  // El álbum del reproductor lleva a su detalle (el artista ya era enlace).
  $("#np-album").addEventListener("click", () => {
    if (!CURRENT_TRACK || !CURRENT_TRACK.album) return;
    switchView("biblioteca");
    switchSub("albums");
    openAlbum(CURRENT_TRACK.album);
  });
  $("#np-album").style.cursor = "pointer";
  $("#np-album").title = "Ir al álbum";
}

let SHOW_REMAINING = false;

// ============ EXPERIENCIA AUDIÓFILA ============
let CURRENT_TRACK = null;
let LYRICS = null;

function wireAudiophile() {
  $("#btn-lyrics").addEventListener("click", async () => {
    const panel = $("#lyrics-panel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) await loadLyrics();
  });
  $("#btn-spectrum").addEventListener("click", async () => {
    if (!CURRENT_TRACK) return toast("No hay pista sonando.", true);
    const panel = $("#spectrum-panel");
    panel.hidden = !panel.hidden;
    if (panel.hidden) return;
    panel.innerHTML = "<div class='hint'>Analizando espectro…</div>";
    const [img, check] = await Promise.all([
      window.pywebview.api.spectrogram(CURRENT_TRACK.path),
      window.pywebview.api.spectral_check(CURRENT_TRACK.path),
    ]);
    let html = "";
    if (check.ok) {
      const cls = check.suspicious ? "sp-warn" : "sp-ok";
      html += `<div class='${cls}'>${escapeHtml(check.verdict)} (corte ~${check.cutoff_khz} kHz de ${check.nyquist_khz} kHz)</div>`;
    }
    html += img.ok ? `<img class='spectrum-img' src='${img.uri}'>` : `<div class='hint'>${escapeHtml(img.error)}</div>`;
    panel.innerHTML = html;
  });

  // EQ
  $("#btn-eq").addEventListener("click", async () => {
    const panel = $("#eq-panel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) await initEq();
  });

  // Auriculares (AutoEq) + crossfeed
  $("#btn-hp").addEventListener("click", async () => {
    const panel = $("#hp-panel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) await initHeadphones();
  });
  $("#hp-select").addEventListener("change", (e) =>
    window.pywebview.api.set_headphone(e.target.value || null)
  );
  $("#btn-hp-import").addEventListener("click", async () => {
    const res = await window.pywebview.api.import_headphone();
    if (!res.ok) return res.error !== "Cancelado." && toast(res.error, true);
    await initHeadphones(true);
    $("#hp-select").value = res.name;
    window.pywebview.api.set_headphone(res.name);
    toast("Importado: " + res.name);
  });
  $("#hp-crossfeed").addEventListener("input", (e) => {
    $("#hp-crossfeed-val").textContent = e.target.value == 0 ? "off" : (e.target.value / 100).toFixed(2);
  });
  $("#hp-crossfeed").addEventListener("change", (e) =>
    window.pywebview.api.set_crossfeed(e.target.value / 100)
  );
  $("#eq-enabled").addEventListener("change", (e) => {
    window.pywebview.api.set_eq(null, e.target.checked);
  });
  $("#eq-reset").addEventListener("click", () => {
    document.querySelectorAll("#eq-sliders .eq-gain").forEach((s) => (s.value = 0));
    sendEq();
  });
  $("#eq-reset-freqs").addEventListener("click", async () => {
    const st = await window.pywebview.api.reset_eq_freqs();
    if (st && st.freqs) applyEqState(st);
  });
  $("#eq-preset-save").addEventListener("click", async () => {
    const name = await promptDialog({ title: "Guardar preset de EQ",
                                      msg: "Nombre del preset:" });
    if (!name) return;
    const res = await window.pywebview.api.save_eq_preset(name);
    if (!res.ok) return toast(res.error, true);
    await loadEqPresets();
    $("#eq-preset-select").value = "u:" + res.name;
    toast(`Preset «${res.name}» guardado.`);
  });
  $("#eq-preset-select").addEventListener("change", async (e) => {
    const raw = e.target.value;
    if (!raw) return;
    const factory = raw.startsWith("f:");
    const st = await window.pywebview.api.load_eq_preset(raw.slice(2), factory);
    if (st && st.gains) {
      $("#eq-enabled").checked = true;
      applyEqState(st);
      if (st.why) $("#eq-why").textContent = st.why;
    }
  });
  $("#eq-preset-del").addEventListener("click", async () => {
    const raw = $("#eq-preset-select").value;
    if (!raw) return toast("Elige un preset primero.", true);
    if (raw.startsWith("f:"))
      return toast("Los presets de fábrica no se borran. Cárgalo, retócalo y guárdalo con otro nombre.", true);
    const name = raw.slice(2);
    const ok = await confirmDialog({ title: "Borrar preset",
                                     msg: `¿Borrar el preset «${name}»?` });
    if (!ok) return;
    await window.pywebview.api.delete_eq_preset(name);
    await loadEqPresets();
    toast("Preset borrado.");
  });

  // --- Convolución (IR) ---
  $("#btn-conv").addEventListener("click", async () => {
    const panel = $("#conv-panel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) await initConvolution();
  });
  $("#btn-conv-import").addEventListener("click", async () => {
    const res = await window.pywebview.api.import_impulse();
    if (!res.ok) return res.error !== "Cancelado." && toast(res.error, true);
    await initConvolution(true);
    $("#conv-select").value = res.name;
    await window.pywebview.api.set_convolution(res.name);
    updateConvInfo();
    toast(`Impulso «${res.name}» importado y activado.`);
  });
  $("#conv-select").addEventListener("change", async (e) => {
    await window.pywebview.api.set_convolution(e.target.value || null);
    updateConvInfo();
  });
  $("#btn-conv-del").addEventListener("click", async () => {
    const name = $("#conv-select").value;
    if (!name) return toast("Elige un impulso primero.", true);
    const ok = await confirmDialog({ title: "Borrar impulso",
                                     msg: `¿Borrar el impulso «${name}»?` });
    if (!ok) return;
    await window.pywebview.api.delete_impulse(name);
    await initConvolution(true);
    toast("Impulso borrado.");
  });
}

let CONV_LIST = [];

async function initConvolution(force = false) {
  const sel = $("#conv-select");
  if (!sel.dataset.loaded || force) {
    CONV_LIST = await window.pywebview.api.list_impulses();
    const cur = await window.pywebview.api.get_convolution();
    sel.innerHTML = '<option value="">Ninguna (sin convolución)</option>' +
      CONV_LIST.map((i) => `<option value="${escapeHtml(i.name)}">${escapeHtml(i.name)}</option>`).join("");
    sel.value = cur.convolution || "";
    sel.dataset.loaded = "1";
  }
  updateConvInfo();
}

function updateConvInfo() {
  const name = $("#conv-select").value;
  const ir = CONV_LIST.find((i) => i.name === name);
  $("#conv-info").textContent = ir
    ? `${ir.channels || "?"} canal(es) · ${ir.sample_rate ? ir.sample_rate / 1000 + " kHz" : "?"}` +
      `${ir.duration ? " · " + ir.duration.toFixed(2) + " s" : ""}`
    : "";
}

// Diálogo de texto (promesa), hermano de confirmDialog.
function promptDialog({ title = "", msg = "", value = "" } = {}) {
  const modal = $("#prompt-modal");
  $("#prompt-title").textContent = title;
  $("#prompt-msg").textContent = msg;
  const input = $("#prompt-input");
  input.value = value;
  modal.hidden = false;
  setTimeout(() => input.focus(), 30);
  return new Promise((resolve) => {
    const onKey = (e) => {
      if (e.key === "Escape") done(null);
      else if (e.key === "Enter") done(input.value.trim() || null);
    };
    const done = (val) => {
      modal.hidden = true;
      $("#btn-prompt-ok").onclick = null;
      $("#btn-prompt-cancel").onclick = null;
      document.removeEventListener("keydown", onKey, true);
      resolve(val);
    };
    $("#btn-prompt-ok").onclick = () => done(input.value.trim() || null);
    $("#btn-prompt-cancel").onclick = () => done(null);
    document.addEventListener("keydown", onKey, true);
  });
}

// --- EQ semiparamétrico: ganancia + frecuencia central por banda -------------

function fmtFreq(f) {
  return f >= 1000 ? +(f / 1000).toFixed(f >= 10000 ? 0 : 1) + "k" : Math.round(f);
}

async function initEq(force = false) {
  const state = await window.pywebview.api.get_eq();
  $("#eq-enabled").checked = state.enabled;
  const box = $("#eq-sliders");
  if (box.children.length && !force) {
    applyEqState(state);
    return;
  }
  box.innerHTML = "";
  state.freqs.forEach((f, i) => {
    const col = document.createElement("div");
    col.className = "eq-band";
    col.innerHTML = `
      <input class="eq-gain" type="range" min="-12" max="12" step="0.5"
             value="${state.gains[i]}" data-band="${i}" orient="vertical">
      <input class="eq-freq" type="number" min="20" max="20000" step="1"
             value="${Math.round(f)}" data-band="${i}" title="Frecuencia central (Hz)">
      <button class="eq-solo" data-band="${i}" title="Mantener pulsado para oír solo esta banda">solo</button>`;
    col.querySelector(".eq-gain").addEventListener("input", () => drawEqCurve());
    col.querySelector(".eq-gain").addEventListener("change", sendEq);
    col.querySelector(".eq-freq").addEventListener("change", sendEq);
    const solo = col.querySelector(".eq-solo");
    // Aislar mientras se mantiene pulsado (idea del "highlight" de FXSound).
    const on = () => window.pywebview.api.set_eq_solo(parseInt(solo.dataset.band));
    const off = () => window.pywebview.api.set_eq_solo(null);
    solo.addEventListener("mousedown", on);
    ["mouseup", "mouseleave", "blur"].forEach((ev) => solo.addEventListener(ev, off));
    box.appendChild(col);
  });
  await loadEqPresets();
  applyEqState(state);
}

function applyEqState(state) {
  document.querySelectorAll("#eq-sliders .eq-gain").forEach((s, i) => {
    if (state.gains[i] !== undefined) s.value = state.gains[i];
  });
  document.querySelectorAll("#eq-sliders .eq-freq").forEach((s, i) => {
    if (state.freqs[i] !== undefined) s.value = Math.round(state.freqs[i]);
  });
  const pre = state.auto_preamp || 0;
  $("#eq-preamp-note").textContent = pre
    ? `Preamp automático ${pre.toFixed(1)} dB aplicado para evitar recorte.`
    : "";
  drawEqCurve();
}

function eqValues() {
  const gains = [...document.querySelectorAll("#eq-sliders .eq-gain")].map((s) => parseFloat(s.value));
  const freqs = [...document.querySelectorAll("#eq-sliders .eq-freq")].map((s) => parseFloat(s.value));
  return { gains, freqs };
}

async function sendEq() {
  const { gains, freqs } = eqValues();
  const state = await window.pywebview.api.set_eq(gains, null, freqs);
  if (state && state.gains) applyEqState(state);
}

// Curva del EQ: interpolación suave sobre escala logarítmica de frecuencia.
function drawEqCurve() {
  const cv = $("#eq-curve");
  if (!cv) return;
  const { gains, freqs } = eqValues();
  if (!gains.length) return;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height, LIMIT = 12;
  ctx.clearRect(0, 0, W, H);

  const xOf = (f) => (Math.log10(Math.max(20, f)) - Math.log10(20)) /
                     (Math.log10(20000) - Math.log10(20)) * W;
  const yOf = (g) => H / 2 - (g / LIMIT) * (H / 2 - 6);

  // Rejilla: 0 dB y ±6 dB
  ctx.strokeStyle = "rgba(255,255,255,0.10)";
  ctx.lineWidth = 1;
  [-6, 0, 6].forEach((g) => {
    ctx.beginPath(); ctx.moveTo(0, yOf(g)); ctx.lineTo(W, yOf(g)); ctx.stroke();
  });

  // Puntos ordenados por frecuencia (las Fc son editables y pueden desordenarse).
  const pts = gains.map((g, i) => ({ x: xOf(freqs[i]), y: yOf(g) }))
                   .sort((a, b) => a.x - b.x);
  ctx.beginPath();
  ctx.moveTo(0, pts[0].y);
  for (let i = 0; i < pts.length - 1; i++) {
    const mx = (pts[i].x + pts[i + 1].x) / 2;
    ctx.bezierCurveTo(mx, pts[i].y, mx, pts[i + 1].y, pts[i + 1].x, pts[i + 1].y);
  }
  ctx.lineTo(W, pts[pts.length - 1].y);
  ctx.strokeStyle = getComputedStyle(document.documentElement)
    .getPropertyValue("--voltage-30").trim() || "#78a9ff";
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.fillStyle = ctx.strokeStyle;
  pts.forEach((p) => { ctx.beginPath(); ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2); ctx.fill(); });
}

async function loadEqPresets() {
  const list = await window.pywebview.api.list_eq_presets();
  const sel = $("#eq-preset-select");
  const current = sel.value;
  // El valor lleva prefijo (f: fábrica / u: usuario) para que dos presets con el
  // mismo nombre no se confundan entre sí.
  const opt = (p) =>
    `<option value="${p.factory ? "f" : "u"}:${escapeHtml(p.name)}"${p.why ? ` title="${escapeHtml(p.why)}"` : ""}>${escapeHtml(p.name)}</option>`;
  const factory = list.filter((p) => p.factory);
  const mine = list.filter((p) => !p.factory);
  sel.innerHTML =
    '<option value="">Presets…</option>' +
    (factory.length ? `<optgroup label="De fábrica">${factory.map(opt).join("")}</optgroup>` : "") +
    (mine.length ? `<optgroup label="Mis presets">${mine.map(opt).join("")}</optgroup>` : "");
  sel.value = current;
}

let hpLoaded = false;
async function initHeadphones(force = false) {
  if (hpLoaded && !force) return;
  hpLoaded = true;
  const cfg = await window.pywebview.api.get_config();
  const list = await window.pywebview.api.list_headphones();
  const sel = $("#hp-select");
  sel.innerHTML = "<option value=''>Ninguna (sin corregir)</option>";
  for (const hp of list) {
    const opt = document.createElement("option");
    opt.value = hp.name;
    opt.textContent = hp.name + (hp.source === "importado" ? " (importado)" : "");
    sel.appendChild(opt);
  }
  sel.value = cfg.headphone_correction || "";
  const cf = Math.round((cfg.crossfeed || 0) * 100);
  $("#hp-crossfeed").value = cf;
  $("#hp-crossfeed-val").textContent = cf == 0 ? "off" : (cf / 100).toFixed(2);
}

async function refreshSignalPath() {
  renderSignalPath(await window.pywebview.api.signal_path());
}

function renderSignalPath(sp) {
  const box = $("#signal-path");
  const badge = sp.bit_perfect
    ? "<span class='sp-badge sp-ok'>bit-perfect</span>"
    : `<span class='sp-badge sp-warn'>${sp.quality}</span>`;
  box.innerHTML =
    `<div class='sp-head'>Signal path ${badge}</div>` +
    sp.stages
      .map((s) => `<div class='sp-stage sp-${s.kind}'>${escapeHtml(s.stage)}: <span class='hint'>${escapeHtml(s.detail)}</span></div>`)
      .join("");
}

async function loadLyrics() {
  const panel = $("#lyrics-panel");
  if (!CURRENT_TRACK) {
    panel.innerHTML = "<div class='hint'>No hay pista sonando.</div>";
    return;
  }
  LYRICS = await window.pywebview.api.get_lyrics(CURRENT_TRACK.path);
  if (!LYRICS.found || !LYRICS.lines.length) {
    panel.innerHTML = "<div class='hint'>Sin letras en este archivo.</div>";
    return;
  }
  panel.innerHTML = LYRICS.lines
    .map((ln, i) => `<div class='lyric-line' id='lyr-${i}'>${escapeHtml(ln.text) || "&nbsp;"}</div>`)
    .join("");
}

function highlightLyric(position) {
  if (!LYRICS || !LYRICS.synced || $("#lyrics-panel").hidden) return;
  let idx = -1;
  for (let i = 0; i < LYRICS.lines.length; i++) {
    if (LYRICS.lines[i].t <= position) idx = i;
    else break;
  }
  document.querySelectorAll(".lyric-line.on").forEach((el) => el.classList.remove("on"));
  if (idx >= 0) {
    const el = $("#lyr-" + idx);
    if (el) {
      el.classList.add("on");
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }
}

// ============ ESTUDIO ============
let studioLoaded = false;
let STUDIO_CHAIN = [];

async function initStudio() {
  studioLoaded = true;
  const avail = await window.pywebview.api.studio_available();
  if (!avail.pedalboard) {
    $("#studio-status").innerHTML =
      "<span class='sp-warn'>El motor de estudio no está instalado.</span> Instala el extra: <code>pip install -e \".[studio]\"</code>";
    return;
  }
  $("#studio-status").innerHTML =
    "Motor listo" + (avail.matchering ? " · mastering por referencia disponible" : "");
  const cat = await window.pywebview.api.studio_catalog();
  const sel = $("#studio-preset");
  sel.innerHTML = "";
  for (const p of cat.presets) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.label;
    opt.dataset.desc = p.description;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", loadStudioPreset);
  await loadStudioPreset();

  $("#btn-studio-preview").addEventListener("click", studioPreview);
  $("#btn-studio-track").addEventListener("click", () => studioProcess(false));
  $("#btn-studio-album").addEventListener("click", () => studioProcess(true));

  window.hub.on("studio:item", (p) => {
    const box = $("#studio-jobs");
    const line = document.createElement("div");
    line.className = "src-row " + (p.ok ? "src-chosen" : "");
    line.textContent = p.ok
      ? `✓ ${p.done}/${p.total} → ${p.dst.split(/[\\/]/).pop()}`
      : `✗ ${(p.error || "error").slice(0, 60)}`;
    box.appendChild(line);
  });
  window.hub.on("studio:complete", (p) => {
    toast("Procesado completo (" + p.total + ")");
    rescanLibrary();
  });
}

async function loadStudioPreset() {
  const sel = $("#studio-preset");
  $("#studio-preset-desc").textContent = sel.selectedOptions[0]?.dataset.desc || "";
  STUDIO_CHAIN = await window.pywebview.api.studio_get_preset(sel.value);
  $("#studio-chain").innerHTML =
    "<div class='hint'>Efectos en la cadena:</div>" +
    STUDIO_CHAIN.map((e) => {
      const params = Object.entries(e)
        .filter(([k]) => k !== "type")
        .map(([k, v]) => `${k}=${v}`)
        .join(", ");
      return `<div class='src-row'>• ${escapeHtml(e.type)} <span class='hint'>${escapeHtml(params)}</span></div>`;
    }).join("");
}

async function studioPreview() {
  if (!CURRENT_TRACK) return toast("Reproduce una pista primero.", true);
  const box = $("#studio-ab");
  box.hidden = false;
  box.innerHTML = "<div class='hint'>Generando vista previa…</div>";
  const res = await window.pywebview.api.studio_preview(CURRENT_TRACK.path, STUDIO_CHAIN);
  if (!res.ok) {
    box.innerHTML = `<div class='hint'>${escapeHtml(res.error)}</div>`;
    return;
  }
  box.innerHTML = "<div class='hint'>Compara (usa el reproductor):</div>";
  const orig = document.createElement("button");
  orig.className = "secondary";
  orig.textContent = "▶ Original";
  orig.onclick = () => window.pywebview.api.player_play_file(res.original);
  const proc = document.createElement("button");
  proc.className = "primary";
  proc.textContent = "▶ Procesado";
  proc.onclick = () => window.pywebview.api.player_play_file(res.processed);
  const row = document.createElement("div");
  row.className = "row inline";
  row.append(orig, proc);
  box.appendChild(row);
}

async function studioProcess(album) {
  if (!CURRENT_TRACK) return toast("Reproduce una pista del álbum a procesar.", true);
  let ids;
  if (album) {
    const tracks = await window.pywebview.api.album_tracks(CURRENT_TRACK.album);
    ids = tracks.map((t) => t.id);
  } else {
    ids = [CURRENT_TRACK.id];
  }
  $("#studio-jobs").innerHTML = "";
  const res = await window.pywebview.api.studio_process({ track_ids: ids, chain: STUDIO_CHAIN });
  if (!res.ok) return toast(res.error, true);
  toast(`Procesando ${res.total} pista(s)…`);
}

function fmtDur(sec) {
  if (!sec && sec !== 0) return "?";
  sec = Math.round(sec);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  const h = Math.floor(m / 60);
  return h ? `${h}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}`
           : `${m}:${String(s).padStart(2, "0")}`;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
  );
}
