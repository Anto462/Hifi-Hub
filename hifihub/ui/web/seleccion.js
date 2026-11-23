"use strict";

// ============================================================================
// SELECCIÓN MÚLTIPLE
// En cualquier tabla de canciones (Canciones, álbum, artista, playlist,
// inteligente): Ctrl+clic alterna una fila, Mayús+clic selecciona un rango,
// Ctrl+A toda la lista (también las otras páginas) y Esc / clic simple la vacía.
// Una barra flotante ofrece las acciones en bloque.
//
// No modifica biblioteca.js: escucha por su cuenta
// y vuelve a marcar las filas tras cada repintado con un
// MutationObserver limitado a los hijos directos de #lib-view, así que el
// coste es un recorrido de las filas visibles por repintado y nada más.
//
// Usa de app.js / biblioteca.js: $, LIB, toast, confirmDialog, changeCover,
// openPlaylistMenuForMany, refreshLibrary, libActive, isTyping, escapeHtml.
// ============================================================================

const SEL = {
  ids: new Set(),   // ids de pista seleccionados
  anchor: -1,       // data-i de la última fila pulsada (origen del rango con Mayús)
  key: "",          // contexto (pestaña + detalle) al que pertenece la selección
  bar: null,
};

// Contexto actual: si cambia (otra pestaña u otro detalle) la selección caduca.
function selKey() {
  const top = LIB.stack[LIB.stack.length - 1];
  return top ? `${top.type}:${top.id}` : `tab:${LIB.tab}`;
}

// Ids de la lista ENTERA del contexto, en su orden (data-i es su posición).
function selContextIds() {
  if (LIB.stack.length) return ((LIB.model && LIB.model.tracks) || []).map((t) => t.id);
  return LIB.ctx && LIB.ctx.kind === "ids" ? LIB.ctx.ids : [];
}

// La selección en el orden de la lista (para reproducir/añadir tal como se ve).
function selOrdered() {
  const seen = new Set();
  return selContextIds().filter((id) => SEL.ids.has(id) && !seen.has(id) && seen.add(id));
}

function selClear() {
  if (!SEL.ids.size) return;
  SEL.ids.clear();
  SEL.anchor = -1;
  selPaint();
}

function selPaint() {
  $("#lib-view").querySelectorAll(".lb-tr[data-track-id]").forEach((r) =>
    r.classList.toggle("sel", SEL.ids.has(+r.dataset.trackId))
  );
  selBar();
}

// --- Barra de acciones -------------------------------------------------------------

function selBar() {
  const n = SEL.ids.size;
  document.body.classList.toggle("has-sel", n > 0);
  if (!n) {
    if (SEL.bar) SEL.bar.hidden = true;
    return;
  }
  if (!SEL.bar) {
    SEL.bar = document.createElement("div");
    SEL.bar.className = "lb-selbar";
    SEL.bar.setAttribute("role", "toolbar");
    SEL.bar.addEventListener("click", (e) => {
      const b = e.target.closest("[data-sel]");
      if (b) selAction(b.dataset.sel, b);
    });
    document.body.appendChild(SEL.bar);
  }
  const top = LIB.stack[LIB.stack.length - 1];
  const mode = top && top.type === "playlist" ? "playlist" : "list";
  // Solo se reconstruye si cambian los botones; si no, basta con el contador.
  if (SEL.bar.dataset.mode !== mode) {
    SEL.bar.dataset.mode = mode;
    const btn = (act, icon, label, cls = "") =>
      `<button data-sel="${act}" class="${cls}" title="${label}"><i class="ph-bold ph-${icon}"></i><span>${label}</span></button>`;
    SEL.bar.innerHTML = `<span class="lb-selcount"><b></b><em></em></span>` +
      btn("play", "play", "Reproducir") + btn("queue", "queue", "A la cola") +
      btn("addpl", "plus", "A playlist") + btn("cover", "image", "Portada") +
      (mode === "playlist" ? btn("unlist", "minus-circle", "Quitar de la playlist") : "") +
      btn("delete", "trash", "Eliminar…", "danger") +
      `<button data-sel="clear" class="ico" title="Vaciar selección (Esc)" aria-label="Vaciar selección"><i class="ph-bold ph-x"></i></button>`;
  }
  SEL.bar.querySelector(".lb-selcount b").textContent = n;
  SEL.bar.querySelector(".lb-selcount em").textContent = n === 1 ? "seleccionada" : "seleccionadas";
  SEL.bar.hidden = false;
}

async function selAction(act, btn) {
  const api = window.pywebview.api;
  const ids = selOrdered();
  if (!ids.length && act !== "clear") return selClear();
  switch (act) {
    case "play": {
      const res = await api.play_ids(ids, 0);
      if (res && res.ok) toast(`Sonando ${ids.length} pista(s).`);
      return;
    }
    case "queue": {
      const res = await api.add_to_queue(ids);
      if (res.ok) toast(`${res.added} añadida(s) a la cola.`);
      return;
    }
    case "addpl":
      return openPlaylistMenuForMany(btn, ids);
    case "cover":
      return changeCover(ids);
    case "unlist": {
      const res = await api.playlist_remove_many(LIB.model.pid, ids);
      toast(`${res.removed} quitada(s) de la playlist.`);
      selClear();
      LIB.data.playlists = null;
      return refreshLibrary();
    }
    case "delete": {
      const ok = await confirmDialog({
        kick: "Eliminar del disco",
        title: `Eliminar ${ids.length} ${ids.length === 1 ? "canción" : "canciones"}`,
        msg: "Se enviarán a la Papelera de reciclaje y se quitarán de la biblioteca. ¿Continuar?",
      });
      if (!ok) return;
      const res = await api.delete_tracks(ids);
      toast(`${res.count} canción(es) enviada(s) a la Papelera.`, !res.ok);
      selClear();
      return refreshLibrary();
    }
    case "clear":
      return selClear();
  }
}

// --- Entrada ----------------------------------------------------------------------

(function wireSelection() {
  const view = $("#lib-view");
  // Ignora controles propios de la fila: botones, enlaces y el número/play.
  const rowOf = (e) => (e.target.closest("button, a, input, select, .artist-link")
    ? null : e.target.closest(".lb-tr[data-track-id][data-i]"));

  // Mayús+clic seleccionaría texto: se evita en el mismo gesto.
  view.addEventListener("mousedown", (e) => {
    if (e.shiftKey && rowOf(e)) e.preventDefault();
  });

  view.addEventListener("click", (e) => {
    const row = rowOf(e);
    if (!row) return;
    const multi = e.ctrlKey || e.metaKey, range = e.shiftKey;
    if (!multi && !range) {           // clic simple: vacía la selección (si la hay)
      if (SEL.ids.size) selClear();
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    SEL.key = selKey();
    const i = +row.dataset.i, id = +row.dataset.trackId;
    if (range && SEL.anchor >= 0) {
      const all = selContextIds();
      const [a, b] = SEL.anchor < i ? [SEL.anchor, i] : [i, SEL.anchor];
      all.slice(a, b + 1).forEach((x) => SEL.ids.add(x));
    } else if (SEL.ids.has(id)) {
      SEL.ids.delete(id);
    } else {
      SEL.ids.add(id);
    }
    SEL.anchor = i;
    selPaint();
  }, true);

  // Ctrl+A y Esc. En CAPTURA y registrado antes que el Esc de biblioteca.js
  // («volver atrás»): con selección activa, Esc solo la vacía.
  document.addEventListener("keydown", (e) => {
    if (!libActive() || isTyping(e)) return;
    const busy = [...document.querySelectorAll(".modal")].some((m) => !m.hidden) ||
      !$("#ctxmenu").hidden || !$("#plmenu").hidden;
    if (busy) return;
    if (e.key === "Escape" && SEL.ids.size) {
      e.preventDefault();
      e.stopImmediatePropagation();
      selClear();
    } else if ((e.ctrlKey || e.metaKey) && (e.key === "a" || e.key === "A")) {
      const ids = selContextIds();
      if (!ids.length || !view.querySelector(".lb-tr[data-track-id]")) return;
      e.preventDefault();
      SEL.key = selKey();
      ids.forEach((x) => SEL.ids.add(x));
      selPaint();
    }
  }, true);

  // Tras cada repintado de la vista: misma lista -> se vuelve a marcar (la
  // selección sobrevive al cambiar de página); otro contexto -> se vacía.
  new MutationObserver(() => {
    if (!SEL.ids.size) return;
    if (selKey() !== SEL.key) {
      SEL.ids.clear();
      SEL.anchor = -1;
    }
    selPaint();
  }).observe(view, { childList: true });

  // Fuera de Biblioteca la barra no tiene sentido: se vacía al salir por
  // cualquier vía (pestañas, wordmark, botones), observando su clase `active`.
  new MutationObserver(() => {
    if (!libActive()) selClear();
  }).observe($("#view-biblioteca"), { attributes: true, attributeFilter: ["class"] });
})();
