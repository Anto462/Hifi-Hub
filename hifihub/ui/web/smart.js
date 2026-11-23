"use strict";

// ============================================================================
// PLAYLISTS INTELIGENTES
// Tarjeta en Biblioteca › Playlists, página de detalle (tipo "smart" del mismo
// historial «Volver a…») y editor de reglas. Las reglas se evalúan en el backend
// (library/smart.py) cada vez que se abre: siempre están al día. El catálogo de
// campos/operadores/órdenes/plantillas también viene del backend, así que la UI
// nunca construye nada que el backend no sepa validar.
//
// Las acciones llevan data-act="smart-…": onLibClick (biblioteca.js) las ignora
// y las atiende este archivo. Usa de app.js / biblioteca.js: $, LIB, libOpen,
// libBack, renderLib, renderDetail, mosaicHTML, openActionMenu, plural, fmtDur,
// escapeHtml, toast, confirmDialog.
// ============================================================================

const SMART = {
  cat: null,         // catálogo del backend (se pide una vez)
  editing: null,     // id de la que se edita (null = nueva)
  timer: 0,          // espera del contador en vivo
  tok: 0,            // descarta respuestas viejas del contador
};

async function smartCatalog() {
  if (!SMART.cat) SMART.cat = await window.pywebview.api.smart_catalog();
  return SMART.cat;
}

// --- Tarjeta y detalle ---------------------------------------------------------------

function smartCard(p) {
  return `<button class="lb-pl lb-pl-smart" data-open-smart="${p.id}" title="${escapeHtml(p.summary || "")}">
      <span class="lb-mo">${mosaicHTML(p.covers, "lb-src")}<span class="lb-band"><span>${escapeHtml(p.name)}</span></span>
        <span class="lb-smart-tag"><i class="ph-bold ph-magic-wand"></i>Inteligente</span></span>
      <small>${plural(p.n, "pista", "pistas")}${p.duration ? " · " + fmtDur(p.duration) : ""}</small>
    </button>`;
}

// Modelo de la página de detalle (lo pide detailModel de biblioteca.js).
async function smartModel(top) {
  const r = await window.pywebview.api.smart_tracks(top.id);
  const tracks = r.ok ? r.tracks : [];
  const covers = [...new Set(tracks.map((t) => t.cover_path).filter(Boolean))].slice(0, 4);
  const dur = tracks.reduce((s, t) => s + (t.duration || 0), 0);
  return {
    type: "smart", sid: top.id, title: r.ok ? r.name : "(ya no existe)", spec: r.ok ? r.spec : null,
    summary: r.ok ? r.summary : r.error, covers, cover: covers[0] || "", tracks,
    meta: `${plural(tracks.length, "pista", "pistas")}${dur ? " · " + fmtDur(dur) : ""}`,
    // Lista explícita de ids: reproducir desde una fila no depende del orden del backend.
    ctx: { kind: "ids", ids: tracks.map((t) => t.id) },
  };
}

async function smartAction(act, el) {
  const api = window.pywebview.api;
  const m = LIB.model;
  switch (act) {
    case "smart-new":
      return openSmartEditor(null);
    case "smart-edit":
      return openSmartEditor(m.sid);
    case "smart-play": {
      const res = await api.play_ids(m.tracks.map((t) => t.id), 0);
      if (res && res.ok) toast(`Sonando: ${m.title}`);
      return;
    }
    case "smart-more":
      return openActionMenu(el, [
        ["Convertir en playlist normal", smartFreeze],
        ["Eliminar playlist inteligente…", smartDelete, true],
      ]);
  }
}

async function smartFreeze() {
  const m = LIB.model;
  const res = await window.pywebview.api.smart_freeze(m.sid);
  if (!res.ok) return toast(res.error, true);
  LIB.data.playlists = null;
  toast(`«${res.name}» creada con ${res.added} pista(s): ya no cambia sola y se puede exportar.`);
}

async function smartDelete() {
  const m = LIB.model;
  const ok = await confirmDialog({
    kick: "Eliminar playlist", title: "Eliminar inteligente",
    msg: `Se eliminará «${m.title}». Solo se borran sus reglas: las canciones siguen en tu biblioteca.`,
  });
  if (!ok) return;
  await window.pywebview.api.smart_delete(m.sid);
  LIB.data.smart = null;
  toast("Playlist inteligente eliminada.");
  libBack();
}

// --- Editor --------------------------------------------------------------------------

async function openSmartEditor(id) {
  const cat = await smartCatalog();
  SMART.editing = id;
  let name = "", spec = { match: "all", rules: [], order: "daily", limit: 0, foryou: false };
  if (id != null) {
    // Desde su página ya están las reglas; si no, se piden.
    const src = LIB.model && LIB.model.sid === id && LIB.model.spec
      ? { name: LIB.model.title, spec: LIB.model.spec }
      : await window.pywebview.api.smart_tracks(id);
    name = src.name;
    spec = src.spec;
  }
  const tpl = $("#sm-template");
  if (tpl.options.length <= 1) {
    tpl.insertAdjacentHTML("beforeend", cat.templates
      .map((t, i) => `<option value="${i}" title="${escapeHtml(t.why)}">${escapeHtml(t.name)}</option>`).join(""));
    $("#sm-order").innerHTML = cat.orders
      .map(([v, l]) => `<option value="${v}">${escapeHtml(l)}</option>`).join("");
  }
  tpl.value = "";
  $("#sm-title").textContent = id == null ? "Nueva inteligente" : "Editar reglas";
  $("#sm-name").value = name;
  smartFill(spec);
  if (id == null && !spec.rules.length) $("#sm-rules").appendChild(smartRuleRow({ field: "genre", op: "contains", value: "" }));
  $("#smart-modal").hidden = false;
  setTimeout(() => $("#sm-name").focus(), 30);
  smartPreview();
}

function smartFill(spec) {
  $("#sm-match").value = spec.match === "any" ? "any" : "all";
  $("#sm-order").value = spec.order || "daily";
  $("#sm-limit").value = spec.limit ? spec.limit : "";
  $("#sm-foryou").checked = !!spec.foryou;
  const box = $("#sm-rules");
  box.innerHTML = "";
  (spec.rules || []).forEach((r) => box.appendChild(smartRuleRow(r)));
}

const smartField = (row) => SMART.cat.fields.find((f) => f.key === row.querySelector(".sm-field").value);

function smartRuleRow(rule) {
  const row = document.createElement("div");
  row.className = "sm-rule";
  row.innerHTML = `<select class="sm-field" aria-label="Campo">${SMART.cat.fields
      .map((f) => `<option value="${f.key}">${escapeHtml(f.label)}</option>`).join("")}</select>
    <select class="sm-op" aria-label="Condición"></select>
    <span class="sm-val"></span>
    <button class="sm-del" type="button" title="Quitar regla" aria-label="Quitar regla"><i class="ph-bold ph-x"></i></button>`;
  row.querySelector(".sm-field").value = rule.field;
  smartOps(row, rule.op);
  smartValue(row, rule.value);
  return row;
}

function smartOps(row, op) {
  const f = smartField(row), sel = row.querySelector(".sm-op");
  sel.innerHTML = f.ops.map(([v, l]) => `<option value="${v}">${escapeHtml(l)}</option>`).join("");
  sel.value = f.ops.some(([v]) => v === op) ? op : f.ops[0][0];
}

// El valor se adapta al tipo: texto, número (con unidad), rango, días o nada.
function smartValue(row, value) {
  const f = smartField(row), op = row.querySelector(".sm-op").value, box = row.querySelector(".sm-val");
  const unit = f.type === "date" ? "días" : f.unit;
  const num = (v, cls = "sm-v") => `<input class="${cls}" type="number" step="any" value="${v ?? ""}">`;
  if (f.type === "bool" || op === "never") box.innerHTML = "";
  else if (f.type === "text") box.innerHTML = `<input class="sm-v" type="text" value="${escapeHtml(value ?? "")}">`;
  else if (op === "between") {
    const [a, b] = Array.isArray(value) ? value : ["", ""];
    box.innerHTML = `${num(a)}<span class="sm-unit">y</span>${num(b, "sm-v2")}${unit ? `<span class="sm-unit">${unit}</span>` : ""}`;
  } else box.innerHTML = num(Array.isArray(value) ? value[0] : value) + (unit ? `<span class="sm-unit">${unit}</span>` : "");
}

function smartReadSpec() {
  const rules = [...$("#sm-rules").querySelectorAll(".sm-rule")].map((row) => {
    const f = smartField(row), op = row.querySelector(".sm-op").value;
    const v = row.querySelector(".sm-v"), v2 = row.querySelector(".sm-v2");
    let value = null;
    if (f.type === "text") value = v ? v.value : "";
    else if (op === "between") value = [v.value, v2.value];
    else if (v) value = v.value;
    return { field: f.key, op, value };
  });
  return {
    match: $("#sm-match").value, rules, order: $("#sm-order").value,
    limit: parseInt($("#sm-limit").value) || 0, foryou: $("#sm-foryou").checked,
  };
}

// Contador en vivo: se espera a que el usuario pare de escribir.
function smartPreview() {
  clearTimeout(SMART.timer);
  SMART.timer = setTimeout(async () => {
    const tok = ++SMART.tok;
    const res = await window.pywebview.api.smart_preview(smartReadSpec());
    if (tok !== SMART.tok) return;
    const out = $("#sm-count");
    out.classList.toggle("err", !res.ok);
    if (!res.ok) out.textContent = res.error;
    else if (res.count !== res.matched) out.innerHTML = `<b>${res.count}</b> pistas <small>(de ${res.matched} que cumplen las reglas)</small>`;
    else out.innerHTML = `<b>${res.count}</b> ${res.count === 1 ? "pista cumple" : "pistas cumplen"} las reglas`;
  }, 250);
}

async function smartSave() {
  const name = $("#sm-name").value.trim();
  const res = await window.pywebview.api.smart_save(SMART.editing, name, smartReadSpec());
  if (!res.ok) {
    $("#sm-count").classList.add("err");
    $("#sm-count").textContent = res.error;
    return;
  }
  $("#smart-modal").hidden = true;
  LIB.data.smart = null;
  const top = LIB.stack[LIB.stack.length - 1];
  if (top && top.type === "smart" && top.id === res.id) return renderDetail(false, null, true);
  if (SMART.editing == null) return libOpen("smart", res.id);   // recién creada: abrirla
  return renderLib(false);
}

(function wireSmart() {
  // Tarjetas y acciones dentro de la Biblioteca.
  $("#lib-view").addEventListener("click", (e) => {
    const open = e.target.closest("[data-open-smart]");
    if (open) return libOpen("smart", +open.dataset.openSmart, open);
    const act = e.target.closest('[data-act^="smart-"]');
    if (act && !act.disabled) smartAction(act.dataset.act, act);
  });

  // Editor: listeners DELEGADOS en el contenedor de reglas (no uno por fila).
  const box = $("#sm-rules");
  box.addEventListener("change", (e) => {
    const row = e.target.closest(".sm-rule");
    if (!row) return;
    if (e.target.matches(".sm-field")) { smartOps(row, null); smartValue(row, null); }
    else if (e.target.matches(".sm-op")) {
      const v = row.querySelector(".sm-v");
      smartValue(row, v ? v.value : null);       // conserva lo escrito al cambiar de operador
    }
    smartPreview();
  });
  box.addEventListener("input", (e) => { if (e.target.matches(".sm-v, .sm-v2")) smartPreview(); });
  box.addEventListener("click", (e) => {
    const del = e.target.closest(".sm-del");
    if (!del) return;
    del.closest(".sm-rule").remove();
    smartPreview();
  });
  ["#sm-match", "#sm-order", "#sm-foryou"].forEach((s) => $(s).addEventListener("change", smartPreview));
  $("#sm-limit").addEventListener("input", smartPreview);
  $("#sm-template").addEventListener("change", (e) => {
    const t = SMART.cat.templates[+e.target.value];
    if (!t) return;
    smartFill({ ...t.spec, foryou: $("#sm-foryou").checked });
    if (!$("#sm-name").value.trim()) $("#sm-name").value = t.name;
    smartPreview();
  });
  $("#btn-sm-add").addEventListener("click", () => {
    $("#sm-rules").appendChild(smartRuleRow({ field: "genre", op: "contains", value: "" }));
    smartPreview();
  });
  $("#btn-sm-save").addEventListener("click", smartSave);
  $("#btn-sm-cancel").addEventListener("click", () => ($("#smart-modal").hidden = true));
  // Esc cierra el editor (en captura: antes que el «volver atrás» de la Biblioteca).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || $("#smart-modal").hidden) return;
    e.stopImmediatePropagation();
    $("#smart-modal").hidden = true;
  }, true);
})();
