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
function confirmDialog({ title = "¿Confirmar?", msg = "", okLabel = "Eliminar", kick = "Confirmar", danger = true } = {}) {
  const modal = $("#confirm-modal");
  modal.classList.toggle("danger", danger);
  $("#confirm-kick").textContent = kick;
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
  paintRange($("#rg-preamp"));

  $("#cfg-autoupd").checked = cfg.auto_check_updates !== false;
  $("#cfg-channel").value = cfg.ytdlp_channel || "stable";

  wireEvents();
  wireSources();
  wireDownloadEvents();
  wireWasapiOpts();
  wireShortcuts();
  wireLibrary(cfg);
  wirePlaylists();
  wireContextMenu();
  wirePlayer();
  wireUpdates();
  wireImport();
  wireLastfm(cfg);
  loadForYou();      // «Para ti» es la vista de arranque
  loadSavedSession(); // la sesión anterior queda preparada en pausa
  renderLib(false);  // la biblioteca se prepara en segundo plano

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

// ============ LAST.FM ============
// Conectar = pedir token + autorizar en el navegador + «Ya lo autoricé». Actúa al
// instante (no espera al botón Guardar de Ajustes) y el secreto nunca vuelve a la
// interfaz: solo se indica si ya hay uno guardado.

function wireLastfm(cfg) {
  $("#cfg-lfm-key").value = cfg.lastfm_api_key || "";
  $("#cfg-lfm-secret").placeholder = cfg.lastfm_has_secret ? "(guardado)" : "";
  renderLastfm(cfg.lastfm_session_key ? cfg.lastfm_username || "tu cuenta" : null);

  $("#btn-lfm-connect").addEventListener("click", async () => {
    const res = await window.pywebview.api.lastfm_connect(
      $("#cfg-lfm-key").value.trim(), $("#cfg-lfm-secret").value.trim());
    if (!res.ok) return toast(res.error, true);
    $("#cfg-lfm-secret").value = "";
    $("#cfg-lfm-secret").placeholder = "(guardado)";
    $("#lfm-status").textContent =
      "Se abrió Last.fm en tu navegador: concede el permiso y vuelve aquí.";
    $("#btn-lfm-finish").hidden = false;
  });
  $("#btn-lfm-finish").addEventListener("click", async () => {
    const res = await window.pywebview.api.lastfm_finish();
    if (!res.ok) return toast(res.error, true);
    renderLastfm(res.username || "tu cuenta");
    toast("Last.fm conectado.");
  });
  $("#btn-lfm-disconnect").addEventListener("click", async () => {
    await window.pywebview.api.lastfm_disconnect();
    renderLastfm(null);
    toast("Last.fm desconectado: ya no se envía nada.");
  });
}

function renderLastfm(user) {
  $("#lfm-status").textContent = user ? `● Conectado como ${user}` : "○ Sin conectar";
  $("#btn-lfm-connect").hidden = !!user;
  $("#btn-lfm-finish").hidden = true;
  $("#btn-lfm-disconnect").hidden = !user;
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
  // Con la nav en una sola línea, la pestaña activa puede quedar fuera de vista.
  const activeTab = document.querySelector(`.tab[data-view="${name}"]`);
  if (activeTab) activeTab.scrollIntoView({ block: "nearest", inline: "nearest" });
  // La escena del tocadiscos solo anima mientras su vista está a la vista.
  const inPlayer = name === "reproduciendo";
  window.NowStage.setActive(inPlayer);
  // Sin nada cargado no se pide la cola: arrancaría libmpv antes de tiempo.
  if (inPlayer && NP_TAB === "queue-inline" && HAS_TRACK) renderQueueInto($("#qi-list"), $("#qi-count"));
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
// La vista vive en biblioteca.js. Aquí quedan las piezas que comparten otras
// vistas: enlaces de artista, menú contextual, menú de playlists, edición y portadas.

function artistLink(name) {
  const label = name || "(sin artista)";
  return `<span class="artist-link" data-artist="${escapeHtml(name || "")}">${escapeHtml(label)}</span>`;
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
    ["Cambiar portada…", () => changeCover([trackId])],
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

// --- Portada elegida a mano ---
// Una pista, un álbum o una selección: la imagen se escribe en los archivos. Si
// suena una de ellas, el backend corta y retoma en el mismo segundo.
async function changeCover(trackIds) {
  const res = await window.pywebview.api.set_cover(trackIds, null);
  if (!res.ok) return res.error !== "Cancelado." && toast(res.error, true);
  toast(res.failed
    ? `Portada aplicada a ${res.updated} pista(s) · ${res.failed} no admiten portada.`
    : `Portada aplicada a ${res.updated} pista(s).`);
  refreshLibrary();
}

// ============ PLAYLISTS ============
function wirePlaylists() {
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
  const uri = await coverUri(coverPath);
  if (uri) el.style.backgroundImage = `url(${uri})`;
}

// Caché de portadas en memoria (LRU con tope de tamaño). En las tablas muchas
// filas comparten álbum: sin caché, cada fila pedía y transfería la misma
// portada (~40 KB en base64). Peticiones simultáneas de la misma portada se
// unen en una sola. Tope ~6 MB de texto (≈150 portadas); se vacía al escanear.
const COVER_CACHE = new Map();
const COVER_PENDING = new Map();
const COVER_CACHE_MAX = 6 * 1024 * 1024;
let COVER_CACHE_SIZE = 0;

function coverUri(path) {
  if (COVER_CACHE.has(path)) {
    const uri = COVER_CACHE.get(path);
    COVER_CACHE.delete(path);          // al final = usada hace poco
    COVER_CACHE.set(path, uri);
    return Promise.resolve(uri);
  }
  if (COVER_PENDING.has(path)) return COVER_PENDING.get(path);
  const p = window.pywebview.api.cover_uri(path).then((uri) => {
    COVER_PENDING.delete(path);
    if (uri) {
      COVER_CACHE.set(path, uri);
      COVER_CACHE_SIZE += uri.length;
      while (COVER_CACHE_SIZE > COVER_CACHE_MAX && COVER_CACHE.size > 1) {
        const [k, v] = COVER_CACHE.entries().next().value;
        COVER_CACHE.delete(k);
        COVER_CACHE_SIZE -= v.length;
      }
    }
    return uri;
  }, () => {
    COVER_PENDING.delete(path);
    return null;
  });
  COVER_PENDING.set(path, p);
  return p;
}

function clearCoverCache() {
  COVER_CACHE.clear();
  COVER_CACHE_SIZE = 0;
}

function shuffled(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// ============ PARA TI ============
// Las mezclas se calculan en el backend a partir de la propia biblioteca (género,
// época, escuchas). Aquí solo se pintan y se conectan a reproducir / guardar.
// El arte (estela del hero, motivos, títulos gigantes) vive en parati-art.js.

let FY_MIXES = [];
let FY_LAST_ALBUM = null;   // para «Retomar» cuando no hay nada cargado

const fyActive = () => $("#view-parati").classList.contains("active");

async function loadForYou() {
  const data = await window.pywebview.api.for_you();
  FY_MIXES = data.mixes || [];
  const strips = data.strips || {};

  // Soltar SOLO los nodos de esta pantalla (nunca el observer entero: otras
  // vistas pueden estar cargando sus portadas al mismo tiempo).
  ["#fy-strip-artists", "#fy-strip-albums", "#fy-strip-added", "#fy-grid"]
    .forEach((sel) => releaseCovers($(sel)));

  renderHero(data.stats || {}, strips);
  renderStrips(strips);
  renderMixes();

  const empty = $("#fy-empty");
  empty.hidden = FY_MIXES.length > 0;
  empty.querySelectorAll("[data-go]").forEach((b) => {
    b.onclick = () => switchView(b.dataset.go);
  });
}

// --- Hero ----------------------------------------------------------------------

function renderHero(st, strips) {
  const art = window.ForYouArt;
  $("#fy-hero-num").textContent = st.plays ? String(st.plays) : "";
  const cal = art.calendar();
  $("#fy-date").textContent = cal.date;
  $("#fy-phase").textContent = cal.phase;

  const stat = (n, label) => (n ? `<div class="fy-stat"><b>${n}</b><small>${label}</small></div>` : "");
  $("#fy-stats").innerHTML =
    stat(st.tracks, "pistas") + stat(st.albums, "álbumes") + stat(st.plays, "reproducciones");

  // Tu #1: su carátula en duotono; pulsarlo lo reproduce y lleva al reproductor.
  const anchor = $("#fy-anchor");
  const top = st.top;
  anchor.hidden = !top;
  if (top) {
    $("#fy-anchor-tag").textContent = `TU #1 · ${top.play_count}×`;
    $("#fy-anchor-title").textContent = top.title || "—";
    $("#fy-anchor-artist").textContent = top.artist || "";
    const img = $("#fy-anchor-img");
    img.style.backgroundImage = "";
    if (top.cover_path) fetchCover(img, top.cover_path);
    anchor.onclick = () => {
      if (top.id == null) return;
      window.pywebview.api.play_track(top.id);
      switchView("reproduciendo");
    };
  }

  FY_LAST_ALBUM = (strips.albums && strips.albums[0] && strips.albums[0].album) || null;
  renderResume();

  // La estela nace en tu #1: se mide cuando el hero ya está maquetado.
  requestAnimationFrame(() => {
    art.buildShards($("#fy-shards-box"), $("#fy-shards"), anchor, true);
    art.animateHero($("#fy-kinetic"),
      [$("#fy-eyebrow"), anchor.hidden ? null : anchor, $("#fy-stats-box")]);
  });
}

// «Continuar» con lo que suena (progreso real) o «Retomar» tu último álbum.
// La app no guarda la posición entre sesiones, así que no se inventa.
function renderResume() {
  const btn = $("#fy-resume");
  if (HAS_TRACK && CURRENT_TRACK) {
    btn.hidden = false;
    btn.dataset.mode = "continue";
    $("#fy-resume-kick").textContent = `Continuar · ${fmtDur(LAST_POS)} / ${fmtDur(NP_DURATION)}`;
    $("#fy-resume-title").textContent = CURRENT_TRACK.title || "—";
    $("#fy-resume-bar").hidden = false;
    $("#fy-resume-bar span").style.width =
      (NP_DURATION ? Math.min(100, (LAST_POS / NP_DURATION) * 100) : 0) + "%";
  } else if (RESUME) {
    // Sesión anterior en pausa: mismo modo «continue» (el play la retoma).
    const t = RESUME.track, dur = t.duration || 0;
    btn.hidden = false;
    btn.dataset.mode = "continue";
    $("#fy-resume-kick").textContent = `Continuar · ${fmtDur(RESUME.position)} / ${fmtDur(dur)}`;
    $("#fy-resume-title").textContent = t.title || "—";
    $("#fy-resume-bar").hidden = false;
    $("#fy-resume-bar span").style.width = (dur ? Math.min(100, (RESUME.position / dur) * 100) : 0) + "%";
  } else if (FY_LAST_ALBUM) {
    btn.hidden = false;
    btn.dataset.mode = "album";
    $("#fy-resume-kick").textContent = "Retomar · último álbum";
    $("#fy-resume-title").textContent = FY_LAST_ALBUM;
    $("#fy-resume-bar").hidden = true;
  } else {
    btn.hidden = true;
  }
}

$("#fy-resume").addEventListener("click", () => {
  if ($("#fy-resume").dataset.mode === "continue") {
    if (!PLAYING) window.pywebview.api.player_toggle();
  } else if (FY_LAST_ALBUM) {
    window.pywebview.api.play_album(FY_LAST_ALBUM);
  }
  switchView("reproduciendo");
});

// Al redimensionar: la estela y los títulos gigantes dependen del ancho.
let FY_RESIZE = 0;
window.addEventListener("resize", () => {
  clearTimeout(FY_RESIZE);
  FY_RESIZE = setTimeout(() => {
    if (!fyActive()) return;
    window.ForYouArt.buildShards($("#fy-shards-box"), $("#fy-shards"), $("#fy-anchor"), false);
    window.ForYouArt.fitGiants($("#fy-grid"));
  }, 150);
});

// --- Clic / doble clic ---------------------------------------------------------
// Un clic abre; un doble clic reproduce y lleva a Reproduciendo. Para distinguirlos
// el clic simple espera ~250 ms (si llega el segundo clic, se cancela). Con
// teclado (detail 0) no hay doble clic posible: se abre al instante.
function clickOrDouble(el, onClick, onDouble) {
  let t = 0;
  el.addEventListener("click", (e) => {
    if (e.target.closest(".fy-alb-play")) return;
    if (e.detail === 0) return onClick();
    if (e.detail > 1) return;
    clearTimeout(t);
    t = setTimeout(onClick, 250);
  });
  el.addEventListener("dblclick", (e) => {
    if (e.target.closest(".fy-alb-play")) return;
    clearTimeout(t);
    onDouble();
  });
}

const playAndShow = (fn) => {
  fn();
  switchView("reproduciendo");
};

// --- Tiras de recientes --------------------------------------------------------
// Cada tipo tiene su propia forma: retratos sobre placa diagonal, fundas con el
// vinilo dentro y un registro de entradas.

const FY_ADDED_MAX = 9;   // el registro se lee en 3 columnas de 3

function renderStrips(strips) {
  renderArtistRail($("#fy-strip-artists"), strips.artists || []);
  renderShelf($("#fy-strip-albums"), strips.albums || []);
  renderAddedLog($("#fy-strip-added"), (strips.added || []).slice(0, FY_ADDED_MAX));
}

function renderArtistRail(section, artists) {
  section.hidden = artists.length === 0;
  if (!artists.length) return;
  const rail = section.querySelector(".fy-rail");
  rail.innerHTML = artists
    .map(
      (a, i) => `<button class="fy-artist" data-artist="${escapeHtml(a.name || "")}" style="--i:${i}"
                   title="Clic: abrir · Doble clic: reproducir">
          <span class="fy-plate"></span>
          <span class="fy-portrait" data-cover="${escapeHtml(a.cover_path || "")}"></span>
          <b>${escapeHtml(a.name || "(sin artista)")}</b>
          <small>${a.plays}× · ${a.tracks} pistas</small>
        </button>`
    )
    .join("");
  rail.querySelectorAll(".fy-artist").forEach((el) => {
    const pic = el.querySelector(".fy-portrait");
    lazyCover(pic, pic.dataset.cover);
    const name = el.dataset.artist || null;
    clickOrDouble(el, () => openArtist(name),
      () => playAndShow(() => window.pywebview.api.play_artist(name, false)));
  });
}

function renderShelf(section, albums) {
  section.hidden = albums.length === 0;
  if (!albums.length) return;
  const rail = section.querySelector(".fy-rail");
  rail.innerHTML = albums
    .map(
      (a, i) => `<div class="fy-alb" role="button" tabindex="0" data-album="${escapeHtml(a.album || "")}" style="--i:${i}"
                   title="Clic: abrir · Doble clic: reproducir">
          <span class="fy-sl">
            <span class="fy-vin"><span class="fy-vin-label" data-cover="${escapeHtml(a.cover_path || "")}"></span></span>
            <span class="fy-sleeve" data-cover="${escapeHtml(a.cover_path || "")}"></span>
            <button class="fy-alb-play" title="Reproducir álbum"><i class="ph-bold ph-play"></i></button>
          </span>
          <span class="fy-alb-txt"><b>${escapeHtml(a.album || "(sin álbum)")}</b>
            <small>${escapeHtml(a.artist || "")} · ${a.tracks} ${a.tracks === 1 ? "pista" : "pistas"}</small></span>
        </div>`
    )
    .join("");
  rail.querySelectorAll(".fy-alb").forEach((el) => {
    el.querySelectorAll("[data-cover]").forEach((c) => lazyCover(c, c.dataset.cover));
    const album = el.dataset.album || null;
    clickOrDouble(el, () => openAlbum(album),
      () => playAndShow(() => window.pywebview.api.play_album(album)));
    el.addEventListener("keydown", (e) => {
      if (e.target !== el || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      e.stopPropagation();
      openAlbum(album);
    });
    // Botón de play: reproduce sin salir de «Para ti» (como hasta ahora).
    el.querySelector(".fy-alb-play").addEventListener("click", (e) => {
      e.stopPropagation();
      window.pywebview.api.play_album(album);
      toast(`Sonando: ${album}`);
    });
  });
}

// Etiqueta de fecha del registro: NUEVO (hoy), AYER, día de la semana o dd/mm.
function addedChip(ts) {
  if (!ts) return { text: "", isNew: false };
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const d = new Date(ts * 1000);
  const diff = Math.round((day(new Date()) - day(d)) / 86400000);
  if (diff <= 0) return { text: "NUEVO", isNew: true };
  if (diff === 1) return { text: "AYER", isNew: false };
  if (diff < 7) return { text: ["DOM", "LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB"][d.getDay()] + ".", isNew: false };
  return { text: `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`, isNew: false };
}

function renderAddedLog(section, albums) {
  section.hidden = albums.length === 0;
  if (!albums.length) return;
  const log = section.querySelector(".fy-log");
  log.innerHTML = albums
    .map((a, i) => {
      const chip = addedChip(a.added);
      return `<button class="fy-row" data-album="${escapeHtml(a.album || "")}" style="--i:${i}"
                title="Clic: abrir · Doble clic: reproducir">
          <span class="fy-ix">${String(i + 1).padStart(2, "0")}</span>
          <span class="fy-thumb" data-cover="${escapeHtml(a.cover_path || "")}"></span>
          <span class="fy-row-t"><b>${escapeHtml(a.album || "(sin álbum)")}</b>
            <small>${escapeHtml(a.artist || "")} · ${a.tracks} ${a.tracks === 1 ? "pista" : "pistas"}</small></span>
          ${chip.text ? `<span class="fy-chip${chip.isNew ? " new" : ""}">${chip.text}</span>` : ""}
        </button>`;
    })
    .join("");
  log.querySelectorAll(".fy-row").forEach((el) => {
    const thumb = el.querySelector(".fy-thumb");
    lazyCover(thumb, thumb.dataset.cover);
    const album = el.dataset.album || null;
    clickOrDouble(el, () => openAlbum(album),
      () => playAndShow(() => window.pywebview.api.play_album(album)));
  });
}

// --- Tus mezclas: pósteres ------------------------------------------------------
// Tono de la rampa voltage + la primera pista de la mezcla al fondo (duotono, con
// el corte diagonal de siempre) + un motivo propio de cada tipo, animado al pasar
// por encima, + el título gigante ajustado al ancho de la tarjeta.

function renderMixes() {
  const art = window.ForYouArt;
  const sec = $("#fy-mixes-sec");
  const grid = $("#fy-grid");
  sec.hidden = FY_MIXES.length === 0;
  const list = art.styleMixes(FY_MIXES);
  grid.innerHTML = list.map(fyPoster).join("");
  grid.querySelectorAll(".fy-mix-cover").forEach((el) => lazyCover(el, el.dataset.cover));

  grid.querySelectorAll(".fy-mix").forEach((card) => {
    const mix = FY_MIXES.find((m) => m.key === card.dataset.key);
    if (!mix) return;
    const play = async () => {
      const res = await window.pywebview.api.play_ids(mix.track_ids, 0);
      if (res && res.ok) toast(`Sonando: ${mix.title}`);
    };
    card.querySelector(".fy-play").addEventListener("click", (e) => {
      e.stopPropagation();
      play();
    });
    card.querySelector(".fy-save").addEventListener("click", (e) => {
      e.stopPropagation();
      openPlaylistMenuForMany(e.currentTarget, mix.track_ids, mix.title);
    });
    card.addEventListener("click", play);
    card.addEventListener("keydown", (e) => {
      if (e.target !== card || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      e.stopPropagation();
      play();
    });
  });
  // El ajuste mide el texto: hace falta la tipografía ya cargada.
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => art.fitGiants(grid));
}

function fyPoster(m, i) {
  const art = window.ForYouArt;
  const style = `--i:${i};--bg:var(${m.tone})` + (m.light ? ";--fg:var(--ink-100)" : "");
  const of = m.total > m.count ? `/ ${m.total} pistas` : "pistas";
  return `<article class="fy-mix m-${m.motif} ${m.cls}${m.light ? " light" : ""}" data-key="${escapeHtml(m.key)}"
            style="${style}" role="button" tabindex="0" aria-label="${escapeHtml(m.title)}, ${m.count} pistas">
      <div class="fy-mix-cover" data-cover="${escapeHtml(m.cover_path || "")}"></div>
      <svg class="motif" viewBox="0 0 400 240" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${art.motif(m.motif, art.hash(m.title))}</svg>
      <span class="sweep" aria-hidden="true"></span>
      <span class="kick"><i></i>${fyKindLabel(m.kind)}</span>
      <span class="desc">${escapeHtml(m.subtitle || "")}</span>
      <span class="count"><b>${m.count}</b><small>${of}</small></span>
      <span class="giant">${escapeHtml(m.title)}</span>
      <span class="acts">
        <button class="fy-play" title="Reproducir la mezcla"><i class="ph-bold ph-play"></i></button>
        <button class="fy-save" title="Guardar como playlist"><i class="ph-bold ph-plus"></i></button>
      </span>
    </article>`;
}

function fyKindLabel(kind) {
  return {
    rotation: "En rotación",
    genre: "Tu mezcla",
    fresh: "Rescate",
    decade: "Cápsula del tiempo",
    revisit: "Vuelve a…",
    smart: "Tu inteligente",   // playlists inteligentes marcadas «Mostrar en Para ti»
  }[kind] || "Mezcla";
}

// ============ REPRODUCTOR ============
let NP_DURATION = 0;
let seeking = false;

function wirePlayer() {
  // Escena del tocadiscos: pulsarla equivale al botón de reproducir/pausa.
  window.NowStage.init({ onToggle: () => window.pywebview.api.player_toggle() });
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
    paintRange(e.target);
  });
  $("#rg-preamp").addEventListener("change", (e) =>
    window.pywebview.api.set_replaygain(null, parseFloat(e.target.value))
  );
  $("#np-seek").addEventListener("mousedown", () => (seeking = true));
  $("#np-seek").addEventListener("input", (e) => paintRange(e.target));
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
  // Al retomar una sesión el backend restaura aleatorio, repetir y volumen: los
  // botones (cuyo estado vive en estas variables locales) se ponen al día.
  window.hub.on("player:modes", (m) => {
    shuffleOn = !!m.shuffle;
    $("#np-shuffle").classList.toggle("on", shuffleOn);
    ri = Math.max(0, repeatModes.indexOf(m.repeat));
    $("#np-repeat").classList.toggle("on", ri !== 0);
    $("#np-repeat").innerHTML = ri === 2 ? '<i class="ph-bold ph-repeat-once"></i>' : '<i class="ph-bold ph-repeat"></i>';
    if (typeof m.volume === "number") syncVolumeUi(m.volume);
  });

  window.hub.on("player:track", async (p) => {
    const t = p.track;
    CURRENT_TRACK = t;
    // Título/artista/álbum: la escena decide el momento (tras la franja diagonal
    // si la vista está visible; al instante si no).
    const paintMeta = () => {
      $("#np-title").textContent = t.title || "—";
      $("#np-artist").innerHTML = t.artist ? artistLink(t.artist) : "";
      $("#np-album").textContent = t.album || "";
    };
    // Ficha técnica: codec, sample rate, profundidad, bitrate, lossless.
    const specs = [];
    if (t.codec) specs.push(t.codec.toUpperCase());
    if (t.sample_rate) specs.push((t.sample_rate / 1000) + " kHz");
    if (t.bit_depth) specs.push(t.bit_depth + "-bit");
    if (t.bit_rate) specs.push(Math.round(t.bit_rate / 1000) + " kbps");
    specs.push(t.lossless ? "lossless" : "lossy");
    $("#np-specs").textContent = specs.join(" · ");
    const uri = t.cover_path ? await coverUri(t.cover_path) : null;
    window.NowStage.setTrack(t, uri, p.index, paintMeta);
    if (fyActive()) renderResume();
    // Barra global + vecinos + resaltado en las listas.
    showMini(t, uri);
    renderNeighbours(p);
    markPlaying();
    refreshOpenQueue();   // la Cola (flotante o integrada) sigue el cambio de pista
    refreshSignalPath();
    if (NP_TAB === "lyrics-panel") loadLyrics();
    // El espectro es de la pista anterior: se ofrece analizar la nueva (es costoso).
    if (NP_TAB === "spectrum-panel") spectrumPrompt();
    else $("#spectrum-panel").innerHTML = "";
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
      paintRange($("#np-seek"));
      $("#mini-fill").style.width = pct + "%";
      highlightLyric(p.position);
    }
    // El brazo del tocadiscos avanza con la pista.
    window.NowStage.setPosition(p.position, p.duration);
    if (fyActive()) renderResume();   // «Continuar» de Para ti con el progreso real
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
  // Dispositivo ocupado en exclusiva por otra app. mpv repite el error mientras
  // dure (el backend lo agrupa cada 5 s), así que la franja se oculta sola cuando
  // deja de llegar: señal de que el dispositivo se liberó y vuelve a sonar.
  // Cerrarla a mano la silencia hasta que termine ESE bloqueo (no reaparece a los 5 s).
  let busyTimer = 0, busyMuted = false;
  window.hub.on("player:device-busy", () => {
    if (!busyMuted) $("#device-banner").hidden = false;
    clearTimeout(busyTimer);
    busyTimer = setTimeout(() => {
      $("#device-banner").hidden = true;
      busyMuted = false;
    }, 12000);
  });
  $("#btn-device-dismiss").addEventListener("click", () => {
    $("#device-banner").hidden = true;
    busyMuted = true;
  });
  $("#btn-device-pick").addEventListener("click", () => {
    $("#device-banner").hidden = true;
    switchView("ajustes");
    $("#cfg-device").focus();
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
  window.NowStage.setPlaying(playing);   // brazo arriba/abajo y giro del disco
  document.querySelectorAll(".lb-tr.playing").forEach((r) =>
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
  const show = (HAS_TRACK || RESUME) && !inPlayer;
  $("#miniplayer").hidden = !show;
  document.body.classList.toggle("has-mini", show);
  if (!show) toggleQueue(false);   // no dejar la cola flotante abierta sin barra
}

// --- Sesión anterior, preparada en pausa ---------------------------------------
// Al abrir la app, la barra global y el «Continuar» de Para ti muestran dónde se
// quedó la escucha. Pedirla NO arranca libmpv: el backend solo lee el JSON y una
// fila. Cualquier play (botones, Espacio, teclas multimedia) la retoma en el
// backend (player_toggle), así que aquí no hace falta tocar esos botones.
let RESUME = null;   // {track, position, volume…} hasta que suene algo

async function loadSavedSession() {
  const s = await window.pywebview.api.saved_session();
  if (!s || !s.ok || HAS_TRACK) return;
  RESUME = s;
  const t = s.track;
  $("#mini-title").textContent = t.title || "—";
  $("#mini-artist").textContent = [t.artist, t.album].filter(Boolean).join(" — ");
  $("#mini-cover").src = t.cover_path ? (await coverUri(t.cover_path)) || "" : "";
  $("#mini-cur").textContent = fmtDur(s.position);
  $("#mini-dur").textContent = fmtDur(t.duration);
  $("#mini-fill").style.width = (t.duration ? Math.min(100, (s.position / t.duration) * 100) : 0) + "%";
  if (typeof s.volume === "number") syncVolumeUi(s.volume);
  updateMiniVisibility();
  if (fyActive()) renderResume();
}

// En cuanto suena algo de verdad, la vista previa deja paso al estado real.
window.hub.on("player:track", () => (RESUME = null));

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
  document.querySelectorAll(".lb-tr.playing").forEach((r) => {
    r.classList.remove("playing", "paused");
    const num = r.querySelector(".track-num");
    if (num && num.dataset.orig !== undefined) {
      num.textContent = num.dataset.orig;
      delete num.dataset.orig;
    }
  });
  if (id === null) return;
  document.querySelectorAll('.lb-tr[data-track-id="' + id + '"]').forEach((r) => {
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
//  - En Reproduciendo: la pestaña «Cola» (#queue-inline), en el flujo. No tapa.
//  - En el resto de vistas: panel FLOTANTE (#queue-panel), superposición rápida.
// El contexto lo decide inQueueView().

function inQueueView() {
  const active = document.querySelector(".view.active");
  return active && active.id === "view-reproduciendo";
}

async function toggleQueue(force) {
  // Ocultar = solo el flotante: la cola del reproductor es una pestaña fija.
  if (force === false) {
    $("#queue-panel").hidden = true;
    return;
  }
  if (inQueueView()) return selectNpTab("queue-inline");
  const panel = $("#queue-panel");
  const show = force !== undefined ? force : panel.hidden;
  panel.hidden = true;
  if (!show) return;
  await renderQueueInto($("#q-list"), $("#q-count"));
  panel.hidden = false;
}

// Refresca el panel de cola que esté abierto (al cambiar de pista).
function refreshOpenQueue() {
  if (!$("#queue-inline").hidden) renderQueueInto($("#qi-list"), $("#qi-count"));
  if (!$("#queue-panel").hidden) renderQueueInto($("#q-list"), $("#q-count"));
}

// Fila compacta del panel flotante.
function queueRow(t, i, idx) {
  return `<div class="q-row ${i === idx ? "now" : i < idx ? "past" : ""}" data-qi="${i}" draggable="true">
            <span class="q-num">${i === idx ? '<i class="ph-bold ph-speaker-high"></i>' : i + 1}</span>
            <span class="q-meta">
              <span class="q-title">${escapeHtml(t.title || "")}</span>
              <span class="q-artist">${escapeHtml(t.artist || "")}</span>
            </span>
            <span class="q-dur">${fmtDur(t.duration)}</span>
          </div>`;
}

// Fila del reproductor (diseño HUD): número en rombo, >>>, miniatura y duración.
function queueItem(t, i, idx) {
  const sub = [t.artist, t.album].filter(Boolean).join(" · ");
  return `<div class="np-qitem ${i === idx ? "now" : i < idx ? "past" : ""}" data-qi="${i}" role="listitem" draggable="true">
            <span class="np-tag"><span>${String(i + 1).padStart(2, "0")}</span></span>
            <span class="np-chev">&gt;&gt;&gt;</span>
            <span class="np-qthumb" data-cover="${escapeHtml(t.cover_path || "")}"></span>
            <span class="np-qmeta"><b>${escapeHtml(t.title || "")}</b><small>${escapeHtml(sub)}</small></span>
            <span class="np-qdur">${fmtDur(t.duration)}</span>
          </div>`;
}

async function renderQueueInto(listEl, countEl) {
  const q = await window.pywebview.api.player_queue();
  const list = q.queue || [];
  const idx = typeof q.index === "number" ? q.index : -1;
  const rich = listEl.id === "qi-list";
  countEl.textContent = list.length ? `${idx + 1} de ${list.length}` : "vacía";
  releaseCovers(listEl);
  listEl.innerHTML = list.length
    ? list.map((t, i) => (rich ? queueItem(t, i, idx) : queueRow(t, i, idx))).join("")
    : `<div class="hint empty">No hay nada en la cola. Reproduce un álbum o una playlist.</div>`;
  if (rich) listEl.querySelectorAll(".np-qthumb").forEach((el) => lazyCover(el, el.dataset.cover));

  listEl.querySelectorAll("[data-qi]").forEach((row) => {
    row.addEventListener("click", async () => {
      await window.pywebview.api.player_play_index(parseInt(row.dataset.qi));
      setTimeout(refreshOpenQueue, 600);
    });
  });
  // Centrar la pista en curso DENTRO de la lista (scrollIntoView movería la página
  // entera, y en el reproductor la cola está siempre a la vista).
  const now = listEl.querySelector(".now");
  if (now) {
    const off = now.getBoundingClientRect().top - listEl.getBoundingClientRect().top;
    listEl.scrollTop += off - (listEl.clientHeight - now.offsetHeight) / 2;
  }
}

// Reordenar la cola arrastrando. Listeners DELEGADOS en la lista (sobreviven a cada
// repintado, una sola vez por lista). Durante el arrastre solo se tocan clases si
// cambia la fila de destino: dragover llega decenas de veces por segundo.
function wireQueueDrag(listEl, countEl) {
  let src = -1, mark = null, after = false;
  const unmark = () => {
    if (mark) mark.classList.remove("drop-before", "drop-after");
    mark = null;
  };
  listEl.addEventListener("dragstart", (e) => {
    const row = e.target.closest("[data-qi]");
    if (!row) return;
    src = +row.dataset.qi;
    row.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(src));
  });
  listEl.addEventListener("dragover", (e) => {
    if (src < 0) return;
    const row = e.target.closest("[data-qi]");
    if (!row) return;
    e.preventDefault();                       // permite soltar aquí
    const r = row.getBoundingClientRect();
    const low = e.clientY > r.top + r.height / 2;
    if (row === mark && low === after) return;
    unmark();
    mark = row;
    after = low;
    row.classList.add(low ? "drop-after" : "drop-before");
  });
  listEl.addEventListener("drop", async (e) => {
    e.preventDefault();
    if (src < 0 || !mark) return;
    // Posición final: delante o detrás de la fila marcada, descontando el hueco
    // que deja la propia pista al salir de su sitio.
    let dst = +mark.dataset.qi + (after ? 1 : 0);
    if (dst > src) dst -= 1;
    const from = src;
    unmark();
    if (dst === from) return;
    await window.pywebview.api.player_move(from, dst);
    const top = listEl.scrollTop;             // no saltar a la pista en curso
    await renderQueueInto(listEl, countEl);
    listEl.scrollTop = top;
  });
  listEl.addEventListener("dragend", () => {
    listEl.querySelectorAll(".dragging").forEach((r) => r.classList.remove("dragging"));
    unmark();
    src = -1;
  });
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
    syncVolumeUi(e.target.value);
    window.pywebview.api.player_volume(e.target.value);
  });
  $("#np-volume").addEventListener("input", (e) => syncVolumeUi(e.target.value));
  syncVolumeUi($("#np-volume").value);
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
  // Arrastrar para reordenar, en las dos presentaciones de la cola.
  wireQueueDrag($("#q-list"), $("#q-count"));
  wireQueueDrag($("#qi-list"), $("#qi-count"));

  // Rueda del ratón sobre cualquiera de los dos volúmenes.
  [$("#np-volume"), $("#mini-volume")].forEach((sl) => {
    sl.addEventListener("wheel", (e) => {
      e.preventDefault();
      const v = Math.max(0, Math.min(100, parseInt(sl.value) + (e.deltaY < 0 ? 3 : -3)));
      syncVolumeUi(v);
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
    openAlbum(CURRENT_TRACK.album);   // «Volver» regresa al reproductor
  });
  $("#np-album").style.cursor = "pointer";
  $("#np-album").title = "Ir al álbum";
}

let SHOW_REMAINING = false;

// Relleno de los deslizadores del reproductor (la pista se pinta hasta --p).
function paintRange(el) {
  if (!el) return;
  const min = parseFloat(el.min) || 0, max = parseFloat(el.max) || 100;
  el.style.setProperty("--p", ((parseFloat(el.value) - min) / (max - min)) * 100 + "%");
}

// Un único sitio para el volumen en pantalla: los dos deslizadores y la cifra.
function syncVolumeUi(v) {
  $("#np-volume").value = v;
  $("#mini-volume").value = v;
  $("#np-vol-val").textContent = Math.round(v);
  paintRange($("#np-volume"));
}

// ============ EXPERIENCIA AUDIÓFILA ============
let CURRENT_TRACK = null;
let LYRICS = null;

// --- Pestañas del reproductor -------------------------------------------------
// Un panel visible cada vez (antes se apilaban). Cada pestaña llama a la misma
// carga que antes hacía su botón.
let NP_TAB = "queue-inline";
const NP_TABS = {
  "queue-inline": () => renderQueueInto($("#qi-list"), $("#qi-count")),
  "lyrics-panel": () => loadLyrics(),
  "spectrum-panel": () => loadSpectrum(),
  "eq-panel": () => initEq(),
  "hp-panel": () => initHeadphones(),
  "conv-panel": () => initConvolution(),
};

async function selectNpTab(pane) {
  if (!NP_TABS[pane]) return;
  NP_TAB = pane;
  document.querySelectorAll(".np-tab").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.pane === pane))
  );
  Object.keys(NP_TABS).forEach((id) => ($("#" + id).hidden = id !== pane));
  await NP_TABS[pane]();
}

async function loadSpectrum() {
  const panel = $("#spectrum-panel");
  if (!CURRENT_TRACK) {
    panel.innerHTML = "<div class='hint'>No hay pista sonando.</div>";
    return;
  }
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
}

// Al cambiar de pista con el Espectro abierto: el análisis es costoso, se ofrece.
function spectrumPrompt() {
  $("#spectrum-panel").innerHTML =
    "<div class='row inline'><span class='hint'>Cambió la pista.</span>" +
    "<button id='btn-spectrum-run' class='secondary'>Analizar esta pista</button></div>";
  $("#btn-spectrum-run").addEventListener("click", loadSpectrum);
}

function wireAudiophile() {
  document.querySelectorAll(".np-tab").forEach((b) =>
    b.addEventListener("click", () => selectNpTab(b.dataset.pane))
  );
  // Flechas ←/→ entre pestañas (patrón de tablist). Sin esto, las flechas harían
  // saltar la canción ±5 s con el foco en una pestaña.
  $(".np-tabs").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    e.stopPropagation();
    const tabs = [...document.querySelectorAll(".np-tab")];
    const i = tabs.indexOf(document.activeElement);
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    next.focus();
    selectNpTab(next.dataset.pane);
  });

  // Auriculares (AutoEq) + crossfeed
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
function promptDialog({ title = "", msg = "", value = "", kick = "Nombre" } = {}) {
  const modal = $("#prompt-modal");
  $("#prompt-kick").textContent = kick;
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
    ? "<span class='np-pill ok'>bit-perfect</span>"
    : `<span class='np-pill warn'>${escapeHtml(sp.quality)}</span>`;
  // Cada etapa lleva su tipo (lossless, lossy, neutral, adjust, dsp, resample)
  // como clase k-*, que colorea la etiqueta.
  box.innerHTML =
    `<h3>Signal path ${badge}</h3>` +
    sp.stages
      .map((s) => `<p class='k-${escapeHtml(s.kind)}'><b>${escapeHtml(s.stage)}:</b> <span>${escapeHtml(s.detail)}</span></p>`)
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
      // Centrar la línea DENTRO del panel (scrollIntoView arrastraría la página).
      const box = $("#lyrics-panel");
      const off = el.getBoundingClientRect().top - box.getBoundingClientRect().top;
      box.scrollTo({ top: box.scrollTop + off - (box.clientHeight - el.offsetHeight) / 2, behavior: "smooth" });
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
