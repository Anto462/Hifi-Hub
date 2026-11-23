"use strict";

// ============================================================================
// Arte de la pantalla «Para ti»: estela de fragmentos del hero, motivos de las
// mezclas, ajuste de los títulos gigantes y entrada del hero.
//
// Solo presentación (no toca la API). Los colores salen de los tokens de
// tokens.css, así el arte sigue al sistema Tanatos.
// ============================================================================

window.ForYouArt = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function rng(a) {
    return () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function hash(s) {
    let h = 2166136261;
    for (const ch of String(s)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    return h >>> 0;
  }

  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // Línea de calendario del hero (estilo Persona, en español).
  function calendar(d = new Date()) {
    const days = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];
    const h = d.getHours();
    const phase = h === 0 ? "Medianoche" : h < 5 ? "Madrugada" : h < 11 ? "Mañana"
      : h < 15 ? "Mediodía" : h < 19 ? "Tarde" : "Noche";
    const date = `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")} ${days[d.getDay()]}`;
    return { date, phase };
  }

  // --- Hero: fragmentos a lo largo de un vector diagonal ----------------------
  // `box` es el contenedor (a todo el ancho) que aloja el <svg>; `anchor` es el
  // elemento del que nace la estela (la carátula del #1). Se mide con offsets de
  // MAQUETACIÓN, no con getBoundingClientRect: la vista entra con una animación
  // de transform y las medidas visuales saldrían desplazadas.
  function buildShards(box, svg, anchor, animate) {
    const r = rng(77);
    svg.innerHTML = "";
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !H) return;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const heroH = box.parentElement.clientHeight;
    let ax, ay;
    if (anchor && !anchor.hidden && anchor.offsetWidth) {
      ax = anchor.offsetLeft - box.offsetLeft + anchor.offsetWidth * 0.5;
      ay = anchor.offsetTop - box.offsetTop + anchor.offsetHeight * 0.4;
    } else {
      ax = -box.offsetLeft + 60;
      ay = heroH - 80;
    }
    const A = { x: ax, y: ay }, B = { x: W + 80, y: -40 };
    const dx = B.x - A.x, dy = B.y - A.y, L = Math.hypot(dx, dy);
    const ux = dx / L, uy = dy / L, nx = -uy, ny = ux;
    const pal = [token("--voltage-deep"), token("--voltage-70"), token("--voltage-60"),
      token("--voltage-40"), token("--voltage-cyan"), "rgba(255,255,255,.85)", "rgba(255,255,255,.12)"];
    const add = (pts, fill, t, extra = {}) => {
      const p = document.createElementNS(NS, "polygon");
      p.setAttribute("points", pts.map((q) => q.map((v) => v.toFixed(1)).join(",")).join(" "));
      p.setAttribute("fill", fill);
      for (const k in extra) p.setAttribute(k, extra[k]);
      p.dataset.t = t;
      svg.append(p);
      return p;
    };
    const P = (t, o) => [A.x + dx * t + nx * o, A.y + dy * t + ny * o];

    // Banda ancha que continúa hacia la página (une el hero con las secciones).
    const C = { x: A.x - dx * 0.35, y: A.y - dy * 0.35 + heroH * 0.2 };
    add([[C.x - 60, C.y + 260], [C.x + 60, C.y + 330], [B.x + 300, B.y + 150], [B.x + 300, B.y - 40]],
      "rgba(15,98,254,.16)", 0);
    const ribbon = (w0, w1, col, j) => {
      const top = [], bot = [];
      for (let i = 0; i <= 14; i++) {
        const t = 0.06 + (i / 14) * 0.94, w = w0 + (w1 - w0) * t;
        top.push(P(t, w * (0.6 + r() * j)));
        bot.unshift(P(t, -w * (0.6 + r() * j)));
      }
      return add(top.concat(bot), col, 0.06);
    };
    ribbon(8, 130, "rgba(255,255,255,.08)", 0.6);
    ribbon(5, 78, token("--voltage-70"), 0.8);
    for (let i = 0; i < 64; i++) {
      const t = Math.pow(r(), 0.85) * 1.05 - 0.05;
      const spread = (24 + Math.max(0, t) * 220) * (r() * 2 - 1);
      const [cx, cy] = P(t, spread);
      const len = 10 + Math.max(0, t) * 80 * r() + 8, wid = 3 + r() * (5 + Math.max(0, t) * 20);
      const sk = (r() - 0.5) * 0.6;
      const pts = [[cx - ux * len, cy - uy * len], [cx + nx * wid + ux * len * sk, cy + ny * wid + uy * len * sk],
        [cx + ux * len * (0.6 + r() * 0.6), cy + uy * len * (0.6 + r() * 0.6)], [cx - nx * wid * 0.7, cy - ny * wid * 0.7]];
      const col = pal[Math.floor(r() * pal.length)];
      add(pts, col, Math.max(0, t), col.includes(".12") ? { stroke: "rgba(255,255,255,.5)", "stroke-width": "1" } : {});
    }
    for (let i = 0; i < 5; i++) {
      const t0 = r() * 0.6, t1 = t0 + 0.15 + r() * 0.3, o = (r() * 2 - 1) * 180;
      const a = P(t0, o), b = P(t1, o + (r() - 0.5) * 30);
      add([a, b, [b[0] + nx * 2.5, b[1] + ny * 2.5]], i % 2 ? token("--voltage-cyan") : token("--voltage-60"), t0);
    }
    // Estela que baja hacia las secciones.
    for (let i = 0; i < 14; i++) {
      const t = -0.05 - r() * 0.35, [cx, cy] = P(t, (r() * 2 - 1) * 90), len = 8 + r() * 26, wid = 2 + r() * 6;
      add([[cx - ux * len, cy - uy * len], [cx + nx * wid, cy + ny * wid], [cx + ux * len, cy + uy * len]],
        pal[Math.floor(r() * 4)], 0);
    }
    if (animate && !reduce) {
      [...svg.children].forEach((p) => {
        const t = +p.dataset.t;
        p.animate(
          [{ transform: `translate(${-ux * 160 * (1 - t)}px,${-uy * 160 * (1 - t)}px)`, opacity: 0 },
           { transform: "translate(0,0)", opacity: 1 }],
          { duration: 700, delay: 100 + t * 500, easing: "cubic-bezier(.2,.8,.2,1)", fill: "backwards" }
        );
      });
    }
  }

  // Entrada del hero: las palabras «saltan» una a una y el resto sube.
  function animateHero(kinetic, rise) {
    if (reduce) return;
    [...kinetic.children].forEach((s, i) => {
      const sk = s.classList.contains("k1") ? "skewX(-12deg) " : "";
      s.animate(
        [{ transform: sk + "translateX(-40px) scale(.6)", opacity: 0 }, { transform: sk + "translateX(0) scale(1)", opacity: 1 }],
        { duration: 520, delay: 350 + i * 130, easing: "cubic-bezier(.2,.9,.2,1.15)", fill: "backwards" }
      );
    });
    rise.filter(Boolean).forEach((el, i) =>
      el.animate([{ transform: "translateY(16px)", opacity: 0 }, { transform: "none", opacity: 1 }],
        { duration: 500, delay: 200 + i * 120, easing: "cubic-bezier(.2,.8,.2,1)", fill: "backwards" })
    );
  }

  // --- Mezclas: motivo, tono y tamaño de celda --------------------------------
  // Se deriva solo del tipo y del título, así funciona con las mezclas de
  // cualquier biblioteca (géneros, décadas… que no se conocen de antemano).
  const GENRE_MOTIFS = ["shards", "slash", "circle", "orbit", "beam", "sun"];
  const TONES = ["--voltage-deep", "--voltage-70", "--voltage-60", "--voltage-40"];

  function styleMixes(list) {
    let lastTone = "";
    const used = new Set();
    const out = list.map((m, i) => {
      const h = hash(m.title);
      let motif, tone, cls = "";
      if (m.kind === "rotation") { motif = "moon"; tone = "--voltage-60"; cls = "feat"; }
      else if (m.kind === "decade") { motif = "arches"; tone = TONES[(i + h) % 3]; }
      else if (m.kind === "fresh") { motif = "dash"; tone = "--voltage-cyan"; }
      else {
        let k = h % GENRE_MOTIFS.length;
        for (let j = 0; j < GENRE_MOTIFS.length && used.has(GENRE_MOTIFS[k]); j++) k = (k + 1) % GENRE_MOTIFS.length;
        motif = GENRE_MOTIFS[k];
        used.add(motif);
        if (used.size === GENRE_MOTIFS.length) used.clear();
        tone = TONES[h % TONES.length];
      }
      if (tone === lastTone && tone !== "--voltage-cyan") tone = TONES[(TONES.indexOf(tone) + 1) % TONES.length];
      lastTone = tone;
      return { ...m, motif, tone, cls, light: tone === "--voltage-cyan" };
    });
    // Celdas anchas para completar la última fila de la rejilla de 12 columnas.
    let rest = out.reduce((a, m) => a + (m.cls === "feat" ? 12 : 3), 0) % 12;
    for (let i = out.length - 1; rest && i >= 0; i--) {
      if (!out[i].cls) { out[i].cls = "wide"; rest = (rest + 3) % 12; }
    }
    return out;
  }

  // Lienzo de 400×240; cada grupo lleva una clase que anima el hover (parati.css).
  function motif(kind, seed) {
    const r = rng(seed);
    const dia = (x, y, sz = 7) =>
      `<rect x="${x - sz / 2}" y="${y - sz / 2}" width="${sz}" height="${sz}" fill="#fff" transform="rotate(45 ${x} ${y})"/>`;
    switch (kind) {
      case "moon":
        return `<g class="mv mv-stripes" fill="rgba(255,255,255,.12)" transform="rotate(-14 200 120)">${
          Array.from({ length: 7 }, (_, i) => `<rect x="-80" y="${18 + i * 32}" width="560" height="${3 + (i % 3) * 5}" style="--d:${i * 40}ms"/>`).join("")
        }</g><g class="mv mv-moon"><circle cx="285" cy="82" r="62" class="f-sun"/><circle cx="300" cy="70" r="55" class="f-bg"/></g>`;
      case "shards":
        return `<g transform="rotate(-24 200 120)">${Array.from({ length: 16 }, () => {
          const x = r() * 400 - 40, y = r() * 240 - 20, w = 20 + r() * 90, k = 0.5 + r();
          const pts = `${x},${y} ${x + w},${y - 6 - r() * 10} ${x + w * 0.7},${y + 8 + r() * 6}`;
          return `<polygon class="mv mv-shard ${r() > 0.4 ? "f-ink" : "f-glass"}" style="--dx:${(26 * k).toFixed(0)}px;--dy:${(-10 * k).toFixed(0)}px;--rt:${((r() - 0.5) * 24).toFixed(0)}deg" points="${pts}"/>`;
        }).join("")}</g>`;
      case "slash":
        return `<polygon class="mv mv-sa f-ink" points="170,0 255,0 120,240 35,240"/><polygon class="mv mv-sb f-glass" points="268,0 282,0 147,240 133,240"/><polygon class="mv mv-sc f-accent" points="300,0 306,0 171,240 165,240" opacity="0"/>`;
      case "circle":
        return `<circle class="mv mv-disc f-mint" cx="285" cy="92" r="60"/><rect class="mv mv-line f-mint" x="284" y="150" width="3" height="90"/>`;
      case "orbit":
        return `<g class="mv mv-orbit"><ellipse cx="250" cy="95" rx="150" ry="38" fill="none" stroke="rgba(255,255,255,.85)" stroke-width="2" transform="rotate(-12 250 95)"/>${dia(108, 122)}${dia(390, 62)}${dia(250, 57, 9)}</g>`;
      case "beam":
        return `<polygon class="mv mv-beam1" points="400,0 400,70 40,240 0,240 0,205" fill="rgba(255,255,255,.14)"/><polygon class="mv mv-beam2 f-accent" points="400,20 400,34 80,240 56,240"/>`;
      case "sun":
        return `<circle class="mv mv-sun f-sun" cx="292" cy="104" r="62"/><circle class="mv mv-halo s-sun" cx="292" cy="104" r="62" fill="none" stroke-width="2" opacity="0"/>`;
      case "dash":
        return `<circle class="mv mv-dash s-ink" cx="290" cy="100" r="56" fill="none" stroke-width="2" stroke-dasharray="6 8"/><path class="mv mv-tri f-ink" d="M280 84 l28 16 l-28 16z"/>`;
      case "arches":
        return [0, 1, 2, 3].map((i) =>
          `<g class="mv mv-arch" style="--d:${i * 70}ms"><path d="M${30 + i * 92} 240 V${130 - i * 16} a36 36 0 0 1 72 0 V240 Z" fill="rgba(0,0,0,.22)"/><path d="M${38 + i * 92} 240 V${132 - i * 16} a28 28 0 0 1 56 0 V240" fill="none" stroke="rgba(255,255,255,.35)" stroke-width="1.5"/></g>`
        ).join("");
    }
    return "";
  }

  // Título gigante: se ajusta al ancho de su tarjeta (sea cual sea el texto).
  function fitGiants(container) {
    container.querySelectorAll(".fy-mix").forEach((card) => {
      const g = card.querySelector(".giant");
      const W = card.clientWidth;
      if (!g || !W) return;
      const max = card.classList.contains("feat") ? 190 : 140;
      g.style.fontSize = "100px";
      const w = g.scrollWidth || 1;
      g.style.fontSize = Math.max(38, Math.min(max, (100 * W * 1.02) / w)) + "px";
    });
  }

  return { calendar, buildShards, animateHero, styleMixes, motif, fitGiants, hash };
})();
