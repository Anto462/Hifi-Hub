"use strict";

// ============================================================================
// Escena de la vista Reproduciendo: funda, vinilo, tocadiscos y brazo.
//
// Es SOLO presentación. No conoce la API: app.js le pasa lo que ya recibe del
// reproductor (pista, estado, posición) y la escena anima en consecuencia. El
// audio real lo lleva mpv; aquí no se toca nada de la cadena de sonido.
//
//  - Primera reproducción: secuencia completa (el vinilo sale de la funda, entra
//    el tocadiscos, el disco va al plato y baja el brazo).
//  - Cambio de pista con el disco montado: cambio RÁPIDO (~1,2 s): el texto se
//    actualiza al instante tras la franja diagonal, la etiqueta del disco se
//    revela en diagonal y el brazo vuelve al inicio. El audio es gapless, así que
//    la escena nunca debe ir segundos por detrás de lo que suena.
//  - Saltos rápidos: solo cuenta la última pista (las intermedias se descartan).
//  - El bucle de animación solo corre con la vista visible y algo que animar.
// ============================================================================

window.NowStage = (() => {
  // Coordenadas de la escena (420×360); se escala entera según el espacio.
  const SC = {
    W: 420, H: 360, cx: 210, cy: 180,
    peek: { x: 328, y: 180 },   // vinilo asomando por la derecha de la funda
    pc: { x: 192, y: 184 },     // centro del plato
    pivot: { x: 340, y: 54 },   // eje del brazo (con el tocadiscos en su sitio)
  };
  const A0 = 19, A1 = 29;       // ángulo del brazo al inicio / final de la pista
  const RPM = 200;              // grados por segundo ≈ 33⅓ rpm
  const LIGHT = { x: 0.28, y: 0.18 };                       // luz fija arriba-izquierda
  const LA = Math.atan2(LIGHT.y - 0.5, LIGHT.x - 0.5) * 180 / Math.PI;
  const GLOW_RGB = "0,229,255"; // --voltage-cyan
  const ART_SIZE = 640;
  // Con el disco girando sin transiciones basta ~30 fps (la cara es casi
  // simétrica); las transiciones van a la tasa completa del monitor.
  const STEADY_MS = 1000 / 30;

  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const T = (ms) => (reduce ? ms * 0.35 : ms);
  const easeIO = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
  const easeOut = (p) => 1 - Math.pow(1 - p, 3);
  const SNAP = "cubic-bezier(.7,0,.3,1)";

  // Estado de las piezas (se interpolan con tween y se pintan en render()).
  const cover = { x: 0, o: 1, s: 1 };
  const tt = { x: 150, o: 0, s: 0.92 };
  const rec = { x: SC.cx, y: SC.cy, s: 1 };
  const arm = { a: 0, lift: 0, mode: "rest" };   // rest | cue | play
  const spin = { a: 0, v: 0 };
  const S = {
    active: false, playing: false, busy: false, mounted: false,
    status: null, key: null, pending: null, seq: 0, pos: 0, dur: 0,
  };
  const ART = new Map();        // portadas ya rasterizadas (por cover_path)
  const tweens = new Set();
  let E = null, gx = null, raf = 0, timer = 0, last = 0, glow = 0, parts = [];
  let onToggle = () => {};

  const $id = (id) => document.getElementById(id);

  // ---------- arranque ----------
  function init(opts = {}) {
    if (opts.onToggle) onToggle = opts.onToggle;
    E = {
      stage: $id("np-stage"), scene: $id("np-scene"), tt: $id("np-tt"),
      platterSpin: $id("np-platter-spin"), led: $id("np-led"),
      rec: $id("np-rec"), halo: $id("np-rec-halo"), recSpin: $id("np-rec-spin"), face: $id("np-rec-face"), next: $id("np-rec-next"),
      sleeve: $id("np-sleeve"), base: $id("np-cover-base"), duo: $id("np-duo"),
      duoCv: $id("np-cover-duo"), nextCv: $id("np-cover-next"), sweep: $id("np-sweep"),
      armWrap: $id("np-arm-wrap"), arm: $id("np-arm"), armShadow: $id("np-arm-shadow"),
      armBase: $id("np-arm-base"), armBase2: $id("np-arm-base2"), glints: $id("np-glints"),
      slash: $id("np-slash"), state: $id("np-state"), cap: $id("np-capno"),
    };
    if (!E.stage) return;
    gx = E.glints.getContext("2d");
    E.stage.style.setProperty("--la", LA.toFixed(1));

    // Toda la escena es el botón de reproducir/pausa (funda, disco o plato).
    E.stage.addEventListener("click", () => onToggle());
    E.stage.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      e.stopPropagation();     // el Espacio global también alternaría: solo una vez
      onToggle();
    });
    addEventListener("resize", () => { if (S.active) { layout(); kick(); } });
    document.addEventListener("visibilitychange", kick);

    const standby = () => {
      if (S.key) return;
      const art = blankArt();
      drawFallback(art, "HiFi Hub", "En espera");
      paintSleeve(art);
      drawRecord(E.face, art);
    };
    standby();
    // Las fuentes del canvas llegan asíncronas: repintar la funda de espera con ellas.
    (document.fonts ? document.fonts.ready : Promise.resolve()).then(standby);
    applyPose();
    render(performance.now(), 0);
    emitState();
  }

  // ---------- API pública (la llama app.js) ----------

  // Nueva pista. `paintMeta` escribe título/artista/álbum: la escena decide CUÁNDO
  // (tras la franja diagonal si la vista está visible; al instante si no).
  async function setTrack(t, coverUri, index, paintMeta) {
    const meta = paintMeta || (() => {});
    if (!E) return meta();
    setCap(index);
    const key = trackKey(t);
    if (!key || key === S.key) return meta();          // misma pista: sin teatro
    const seq = ++S.seq;
    const art = await buildArt(t, coverUri);
    if (seq !== S.seq) return;                         // llegó otra más nueva: manda esa
    if (!S.active) {                                   // vista oculta: pintar y listo
      S.key = key;
      paintSleeve(art);
      drawRecord(E.face, art);
      meta();
      emitState();
      return;
    }
    if (S.busy) {                                      // a mitad de una secuencia
      meta();
      S.pending = { key, art };
      return;
    }
    await change(key, art, meta);
  }

  function setPlaying(playing) {
    playing = !!playing;
    const changed = playing !== S.playing;
    S.playing = playing;
    emitState();
    kick();
    if (!E || !changed || !S.active || S.busy) return;
    if (S.mounted) settle();
    else if (playing && S.key) mountAndSettle();
  }

  function setPosition(pos, dur) {
    if (typeof dur === "number") S.dur = dur;
    if (typeof pos === "number") S.pos = pos;
    kick();
  }

  // La vista se muestra / se oculta. Oculta = cero trabajo de animación.
  function setActive(on) {
    if (!E || on === S.active) return;
    S.active = on;
    if (!on) {
      flushTweens();
      if (raf) cancelAnimationFrame(raf);
      clearTimeout(timer);
      raf = timer = 0;
      return;
    }
    layout();
    applyPose();
    render(performance.now(), 0);
    kick();
    // Primera vez que se ve con algo sonando: la secuencia completa, una sola vez.
    if (!S.mounted && S.key && S.playing && !S.busy) mountAndSettle();
  }

  // ---------- secuencias ----------

  async function change(key, art, meta) {
    S.busy = true;
    S.key = key;
    if (S.mounted) {
      await fastChange(art, meta);
    } else {
      setStatus("CHANGING");
      await Promise.all([coverSwap(art), slashSwap(meta)]);
      drawRecord(E.face, art);
      if (S.playing) await mountSeq();
    }
    S.busy = false;
    S.status = null;
    emitState();
    await settle();
    runPending();
  }

  async function mountAndSettle() {
    S.busy = true;
    await mountSeq();
    S.busy = false;
    S.status = null;
    emitState();
    await settle();
    runPending();
  }

  function runPending() {
    const p = S.pending;
    S.pending = null;
    if (p && p.key !== S.key && !S.busy) change(p.key, p.art, () => {});
  }

  // Funda → vinilo asoma → la funda se va y entra el tocadiscos → disco al plato → brazo.
  async function mountSeq() {
    setStatus("LOADING");
    await tween(rec, { x: SC.peek.x }, 520, easeOut);
    await wait(160);
    await Promise.all([
      tween(cover, { x: -150, o: 0, s: 0.92 }, 620),
      (async () => { await wait(120); await tween(tt, { x: 0, o: 1, s: 1 }, 640, easeOut); })(),
      (async () => {
        await tween(rec, { s: 1.05 }, 180, easeOut);
        await wait(180);
        await tween(rec, { x: SC.pc.x, y: SC.pc.y }, 620);
      })(),
    ]);
    await tween(rec, { s: 1 }, 220, easeOut);
    burst(14);
    S.mounted = true;
    setStatus("CUEING");
    arm.mode = "cue";
    await tween(arm, { lift: 1 }, 150);
    await tween(arm, { a: armTarget() }, 650);
  }

  // Cambio con el disco ya en el plato: brazo al inicio + etiqueta nueva + texto.
  async function fastChange(art, meta) {
    setStatus("CHANGING");
    arm.mode = "cue";
    await Promise.all([
      (async () => { await tween(arm, { lift: 1 }, 150); await tween(arm, { a: A0 }, 520); })(),
      (async () => {
        await tween(rec, { s: 1.05 }, 180, easeOut);
        await revealRecord(art);
        await tween(rec, { s: 1 }, 220, easeOut);
        burst(10);
      })(),
      slashSwap(meta),
    ]);
    paintSleeve(art);   // la funda queda lista para cuando vuelva a verse
  }

  // Deja el brazo como manda el estado (bajado sonando, levantado en pausa).
  async function settle() {
    if (!S.mounted) return;
    if (S.playing) {
      arm.mode = "play";
      await tween(arm, { lift: 0 }, 260, easeOut);
    } else {
      arm.mode = "cue";
      await tween(arm, { lift: 1 }, 200);
    }
  }

  // Funda: duotono → barrido diagonal con la portada nueva → vuelta al color.
  async function coverSwap(art) {
    if (!S.active) return paintSleeve(art);
    const cd = E.duoCv.getContext("2d");
    const cn = E.nextCv.getContext("2d");
    cd.clearRect(0, 0, ART_SIZE, ART_SIZE);
    cd.drawImage(E.base, 0, 0);
    await E.duo.animate([{ opacity: 0 }, { opacity: 1 }], { duration: T(300), fill: "forwards" }).finished;
    cn.clearRect(0, 0, ART_SIZE, ART_SIZE);
    cn.drawImage(art, 0, 0);
    E.sweep.animate(
      [{ transform: "translateX(-120%) skewX(-24deg)" }, { transform: "translateX(120%) skewX(-24deg)" }],
      { duration: T(520), easing: SNAP }
    );
    await E.nextCv.animate(
      [{ clipPath: "polygon(0 0,0% 0,-40% 100%,0 100%)" }, { clipPath: "polygon(0 0,140% 0,100% 100%,0 100%)" }],
      { duration: T(520), easing: SNAP, fill: "forwards" }
    ).finished;
    paintSleeve(art);
    cd.clearRect(0, 0, ART_SIZE, ART_SIZE);
    cd.drawImage(art, 0, 0);
    E.nextCv.getAnimations().forEach((a) => a.cancel());
    await wait(140);
    await E.duo.animate([{ opacity: 1 }, { opacity: 0 }], { duration: T(480), fill: "forwards" }).finished;
  }

  // Etiqueta del disco: la nueva se revela en diagonal sobre la anterior.
  async function revealRecord(art) {
    drawRecord(E.next, art);
    if (S.active) {
      await E.next.animate(
        [{ clipPath: "polygon(0 0,0% 0,-40% 100%,0 100%)" }, { clipPath: "polygon(0 0,140% 0,100% 100%,0 100%)" }],
        { duration: T(480), easing: SNAP, fill: "forwards" }
      ).finished;
    }
    drawRecord(E.face, art);
    E.next.getAnimations().forEach((a) => a.cancel());
    E.next.getContext("2d").clearRect(0, 0, E.next.width, E.next.height);
  }

  // Franja diagonal que barre título/artista/álbum; el texto cambia tapado.
  async function slashSwap(meta) {
    if (!S.active || reduce || !E.slash) return meta();
    await E.slash.animate(
      [{ transform: "translateX(-110%) skewX(-24deg)" }, { transform: "translateX(0%) skewX(-24deg)" }],
      { duration: 320, easing: SNAP, fill: "forwards" }
    ).finished;
    meta();
    await E.slash.animate(
      [{ transform: "translateX(0%) skewX(-24deg)" }, { transform: "translateX(110%) skewX(-24deg)" }],
      { duration: 360, easing: SNAP, fill: "forwards" }
    ).finished;
  }

  // ---------- tweens y bucle ----------

  function tween(o, to, ms, ease = easeIO) {
    // Vista oculta: no hay fotogramas, así que se llega al destino al instante.
    if (!S.active) { Object.assign(o, to); return Promise.resolve(); }
    return new Promise((res) => {
      const from = {};
      for (const k in to) from[k] = o[k];
      tweens.add({ o, from, to, t: 0, d: Math.max(0.001, T(ms) / 1000), ease, res });
      kick();
    });
  }

  function flushTweens() {
    for (const tw of tweens) { Object.assign(tw.o, tw.to); tw.res(); }
    tweens.clear();
  }

  const wait = (ms) => (S.active ? new Promise((r) => setTimeout(r, T(ms))) : Promise.resolve());

  function armTarget() {
    const p = S.dur ? Math.min(1, Math.max(0, S.pos / S.dur)) : 0;
    return A0 + (A1 - A0) * p;
  }

  function needsFrames() {
    if (!S.active || document.hidden) return false;
    return tweens.size > 0 || parts.length > 0 || spin.v > 0 || glow > 0.005 ||
      (S.playing && S.mounted) ||
      (arm.mode === "play" && Math.abs(arm.a - armTarget()) > 0.05);
  }

  function kick() {
    if (raf || !E || !S.active || document.hidden) return;
    // Si había un fotograma "lento" programado, se adelanta; si estaba parado,
    // se reinicia el reloj para no dar un salto.
    if (timer) { clearTimeout(timer); timer = 0; } else last = performance.now();
    raf = requestAnimationFrame(frame);
  }

  function scheduleNext() {
    if (!needsFrames()) return;
    if (tweens.size) { raf = requestAnimationFrame(frame); return; }
    timer = setTimeout(() => { timer = 0; raf = requestAnimationFrame(frame); }, STEADY_MS);
  }

  function frame(now) {
    raf = 0;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    for (const tw of tweens) {
      tw.t += dt;
      const p = Math.min(1, tw.t / tw.d);
      const e = tw.ease(p);
      for (const k in tw.to) tw.o[k] = tw.from[k] + (tw.to[k] - tw.from[k]) * e;
      if (p >= 1) { tweens.delete(tw); tw.res(); }
    }
    // Brillo DECORATIVO: un pulso suave mientras suena (el audio no pasa por aquí).
    const tg = S.playing && S.mounted ? 0.22 + 0.1 * (1 + Math.sin(now / 650)) : 0;
    glow += (tg - glow) * Math.min(1, dt * (tg > glow ? 6 : 3));
    if (tg === 0 && glow < 0.005) glow = 0;
    const target = S.playing && S.mounted ? (reduce ? 60 : RPM) : 0;
    spin.v += (target - spin.v) * Math.min(1, dt * (target ? 1.8 : 1.2));
    if (!target && spin.v < 2) spin.v = 0;     // a <2°/s ya no se aprecia: parar el bucle
    spin.a = (spin.a + spin.v * dt) % 360;
    if (arm.mode === "play" && !S.busy) arm.a += (armTarget() - arm.a) * Math.min(1, dt * 3);
    render(now, dt);
    scheduleNext();
  }

  // Escribe un estilo/atributo SOLO si cambió: con el disco girando, casi todas
  // las piezas están quietas y reescribirlas invalidaría estilos en cada fotograma.
  const shown = new Map();
  function put(el, key, val) {
    const k = el.id + key;
    if (shown.get(k) === val) return;
    shown.set(k, val);
    if (key[0] === "@") el.setAttribute(key.slice(1), val);
    else if (key[0] === "-") el.style.setProperty(key, val);
    else el.style[key] = val;
  }

  function render(now, dt) {
    put(E.halo, "opacity", Math.min(1, glow * 1.6).toFixed(2));
    const rot = `rotate(${spin.a.toFixed(1)}deg)`;
    put(E.recSpin, "transform", rot);
    put(E.platterSpin, "transform", rot);
    put(E.sleeve, "transform", `translateX(${cover.x.toFixed(1)}px) scale(${cover.s.toFixed(3)})`);
    put(E.sleeve, "opacity", cover.o.toFixed(3));
    put(E.tt, "transform", `translateX(${tt.x.toFixed(1)}px) scale(${tt.s.toFixed(3)})`);
    put(E.tt, "opacity", tt.o.toFixed(3));
    put(E.rec, "transform", `translate(${(rec.x - 125).toFixed(1)}px,${(rec.y - 125).toFixed(1)}px) scale(${rec.s.toFixed(3)})`);
    put(E.rec, "--lift", ((rec.s - 1) / 0.05).toFixed(2));
    const px = (SC.pivot.x + tt.x).toFixed(1), py = SC.pivot.y;
    const a = arm.a.toFixed(2);
    put(E.armWrap, "@opacity", tt.o.toFixed(3));
    for (const b of [E.armBase, E.armBase2]) { put(b, "@cx", px); put(b, "@cy", py); }
    put(E.arm, "@transform", `translate(${px} ${py}) rotate(${a})`);
    put(E.armShadow, "@transform",
      `translate(${(+px + 3 + arm.lift * 7).toFixed(1)} ${(py + 4 + arm.lift * 10).toFixed(1)}) rotate(${a})`);
    put(E.armShadow, "@opacity", (0.35 - arm.lift * 0.15).toFixed(2));
    drawGlints(dt, now);
  }

  function layout() {
    const w = E.stage.clientWidth;
    if (!w) return;
    // Acotada también por la ALTURA de la ventana: a 900×680 el transporte tiene
    // que seguir viéndose sin hacer scroll.
    const sc = Math.min(1.2, w / SC.W, Math.max(0.6, (innerHeight * 0.46) / SC.H));
    E.scene.style.transform = `translate(${(w - SC.W * sc) / 2}px,0) scale(${sc})`;
    E.stage.style.height = SC.H * sc + "px";
    const d = Math.min(2, devicePixelRatio || 1) * sc;
    E.glints.style.width = SC.W + "px";
    E.glints.style.height = SC.H + "px";
    E.glints.width = Math.round(SC.W * d);
    E.glints.height = Math.round(SC.H * d);
    gx.setTransform(d, 0, 0, d, 0, 0);
  }

  // Coloca las piezas según el estado, sin animar (al entrar en la vista).
  function applyPose() {
    if (S.busy) return;
    if (S.mounted) {
      Object.assign(cover, { x: -150, o: 0, s: 0.92 });
      Object.assign(tt, { x: 0, o: 1, s: 1 });
      Object.assign(rec, { x: SC.pc.x, y: SC.pc.y, s: 1 });
      arm.a = armTarget();
      arm.lift = S.playing ? 0 : 1;
      arm.mode = S.playing ? "play" : "cue";
    } else {
      Object.assign(cover, { x: 0, o: 1, s: 1 });
      Object.assign(tt, { x: 150, o: 0, s: 0.92 });
      Object.assign(rec, { x: SC.cx, y: SC.cy, s: 1 });
      Object.assign(arm, { a: 0, lift: 0, mode: "rest" });
    }
  }

  // ---------- HUD ----------

  function setStatus(s) {
    S.status = s;
    emitState();
  }

  function emitState() {
    if (!E) return;
    const st = S.status || (S.playing ? "PLAYING" : S.key ? "PAUSED" : "STANDBY");
    if (E.state) E.state.textContent = st;
    E.led.classList.toggle("on", S.playing);
    E.stage.setAttribute("aria-label", S.playing ? "Pausar" : "Reproducir");
  }

  function setCap(index) {
    if (!E.cap) return;
    E.cap.textContent = Number.isInteger(index) && index >= 0 ? String(index + 1).padStart(2, "0") : "--";
  }

  // ---------- destellos (luz fija arriba-izquierda) ----------

  function star(x, y, s, a) {
    if (s < 0.5 || a <= 0) return;
    gx.globalAlpha = Math.min(1, a);
    const g = gx.createRadialGradient(x, y, 0, x, y, s * 0.9);
    g.addColorStop(0, "rgba(255,255,255,.9)");
    g.addColorStop(0.3, `rgba(${GLOW_RGB},.35)`);
    g.addColorStop(1, "rgba(255,255,255,0)");
    gx.fillStyle = g;
    gx.beginPath(); gx.arc(x, y, s * 0.9, 0, 7); gx.fill();
    gx.fillStyle = "#fff";
    gx.beginPath();
    gx.moveTo(x - s * 1.6, y);
    gx.quadraticCurveTo(x, y, x, y - s * 1.6);
    gx.quadraticCurveTo(x, y, x + s * 1.6, y);
    gx.quadraticCurveTo(x, y, x, y + s * 1.6);
    gx.quadraticCurveTo(x, y, x - s * 1.6, y);
    gx.fill();
    gx.globalAlpha = 1;
  }

  function burst(n) {
    if (reduce || !S.active) return;
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.283, sp = 40 + Math.random() * 110;
      parts.push({ x: rec.x + Math.cos(a) * 123, y: rec.y + Math.sin(a) * 123,
        vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, life: 0.6 + Math.random() * 0.6, max: 1.2,
        s: 2 + Math.random() * 4 });
    }
    kick();
  }

  function drawGlints(dt, now) {
    gx.clearRect(0, 0, SC.W, SC.H);
    gx.globalCompositeOperation = "lighter";
    const rad = LA * Math.PI / 180, tw = 0.55 + 0.45 * Math.sin(now / 170);
    const base = 3 + glow * 9, R = 125 * rec.s;
    if (S.mounted) {
      star(rec.x + Math.cos(rad) * R * 0.98, rec.y + Math.sin(rad) * R * 0.98, base * 1.3 * tw, 0.95);
      star(rec.x + Math.cos(rad + Math.PI) * R * 0.6, rec.y + Math.sin(rad + Math.PI) * R * 0.6,
        base * 0.6 * (1.4 - tw), 0.5);
      if (!reduce && Math.random() < (spin.v / 300) * 0.2) {
        const a = rad + (Math.random() - 0.5) * 1.1, d = 0.4 + Math.random() * 0.56;
        parts.push({ x: rec.x + Math.cos(a) * R * d, y: rec.y + Math.sin(a) * R * d, vx: 0, vy: 0,
          life: 0.25, max: 0.25, s: 1.5 + Math.random() * 2.5 + glow * 3 });
      }
    }
    parts = parts.filter((p) => (p.life -= dt) > 0);
    for (const p of parts) {
      p.x += p.vx * dt; p.y += p.vy * dt; p.vx *= 0.96; p.vy *= 0.96;
      const k = p.life / p.max;
      star(p.x, p.y, p.s * Math.sin(k * Math.PI), k * 1.2);
    }
    gx.globalCompositeOperation = "source-over";
  }

  // ---------- portadas y disco ----------

  function trackKey(t) {
    if (!t) return null;
    if (t.id != null) return "id:" + t.id;
    return t.path ? "p:" + t.path : null;
  }

  function blankArt() {
    const cv = document.createElement("canvas");
    cv.width = cv.height = ART_SIZE;
    return cv;
  }

  async function buildArt(t, uri) {
    const ck = t.cover_path || "fb:" + (t.title || "") + "|" + (t.artist || "");
    if (ART.has(ck)) return ART.get(ck);
    const cv = blankArt();
    let ok = false;
    if (uri) {
      try {
        const img = new Image();
        img.src = uri;
        await img.decode();
        drawImageCover(cv, img);
        ok = true;
      } catch (e) { /* portada ilegible: se genera una */ }
    }
    if (!ok) drawFallback(cv, t.title, t.artist);
    ART.set(ck, cv);
    if (ART.size > 24) ART.delete(ART.keys().next().value);
    return cv;
  }

  function drawImageCover(cv, img) {
    const ctx = cv.getContext("2d"), S2 = cv.width;
    const w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
    const side = Math.min(w, h);
    ctx.drawImage(img, (w - side) / 2, (h - side) / 2, side, side, 0, 0, S2, S2);
  }

  function paintSleeve(art) {
    const ctx = E.base.getContext("2d");
    ctx.clearRect(0, 0, ART_SIZE, ART_SIZE);
    ctx.drawImage(art, 0, 0);
  }

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

  // Portada generada para pistas sin carátula: arcos voltage + título en Antonio.
  function drawFallback(cv, title, artist) {
    const ctx = cv.getContext("2d"), S2 = cv.width, k = S2 / 640;
    const seed = hash((title || "") + "|" + (artist || "")), r = rng(seed);
    const h = 210 + (seed % 30);
    ctx.save();
    ctx.clearRect(0, 0, S2, S2);
    ctx.fillStyle = `hsl(${h} 75% 9%)`;
    ctx.fillRect(0, 0, S2, S2);
    ctx.lineCap = "round";
    for (let i = 0; i < 12; i++) {
      ctx.beginPath();
      ctx.arc(0, S2, S2 * (0.15 + i * 0.075), -Math.PI / 2, 0);
      ctx.lineWidth = S2 * (0.012 + r() * 0.03);
      ctx.strokeStyle = `hsl(${h - 24 + i * 5} 90% ${38 + i * 3}%)`;
      ctx.stroke();
    }
    for (let i = 0; i < 900; i++) {
      ctx.fillStyle = `rgba(255,255,255,${r() * 0.05})`;
      ctx.fillRect(r() * S2, r() * S2, k, k);
    }
    ctx.textBaseline = "alphabetic";
    ctx.font = `500 ${15 * k}px "IBM Plex Mono", monospace`;
    ctx.fillStyle = "rgba(255,255,255,.75)";
    ctx.fillText(String(artist || "").toUpperCase().split("").join(" "), 26 * k, 42 * k);
    const tt2 = String(title || "—").toUpperCase();
    let fs = 76 * k;
    ctx.font = `700 ${fs}px Antonio, "Arial Narrow", sans-serif`;
    while (ctx.measureText(tt2).width > S2 - 56 * k && fs > 30 * k) {
      fs -= 4 * k;
      ctx.font = `700 ${fs}px Antonio, "Arial Narrow", sans-serif`;
    }
    ctx.fillStyle = "#fff";
    ctx.shadowColor = "rgba(0,0,0,.35)";
    ctx.shadowBlur = 12 * k;
    ctx.fillText(tt2, 28 * k, S2 - 46 * k);
    ctx.restore();
  }

  // Vinilo: surcos, pistas separadas, borde y la portada como etiqueta central.
  function drawRecord(cv, art) {
    const ctx = cv.getContext("2d"), W = cv.width, c = W / 2, R = W / 2 - 1, k = W / 500;
    const r = rng(7);
    ctx.clearRect(0, 0, W, W);
    ctx.beginPath(); ctx.arc(c, c, R, 0, 7); ctx.fillStyle = "#0a0a0d"; ctx.fill();
    for (let rr = R * 0.37; rr < R * 0.97; rr += 1.7 * k) {
      ctx.strokeStyle = `rgba(255,255,255,${0.018 + r() * 0.05})`;
      ctx.lineWidth = 0.8 * k;
      ctx.beginPath(); ctx.arc(c, c, rr, 0, 7); ctx.stroke();
    }
    [0.52, 0.66, 0.8].forEach((d) => {
      ctx.strokeStyle = "rgba(0,0,0,.9)"; ctx.lineWidth = 3 * k;
      ctx.beginPath(); ctx.arc(c, c, R * d, 0, 7); ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,.08)"; ctx.lineWidth = 1 * k;
      ctx.beginPath(); ctx.arc(c, c, R * d + 2 * k, 0, 7); ctx.stroke();
    });
    ctx.strokeStyle = "rgba(255,255,255,.18)"; ctx.lineWidth = 3 * k;
    ctx.beginPath(); ctx.arc(c, c, R * 0.985, 0, 7); ctx.stroke();
    ctx.save();
    ctx.beginPath(); ctx.arc(c, c, R * 0.35, 0, 7); ctx.clip();
    ctx.drawImage(art, 0, 0, art.width, art.height, c - R * 0.35, c - R * 0.35, R * 0.7, R * 0.7);
    ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 2 * k;
    ctx.beginPath(); ctx.arc(c, c, R * 0.3, 0, 7); ctx.stroke();
    ctx.restore();
    ctx.globalCompositeOperation = "destination-out";
    ctx.beginPath(); ctx.arc(c, c, R * 0.028, 0, 7); ctx.fill();
    ctx.globalCompositeOperation = "source-over";
  }

  return { init, setTrack, setPlaying, setPosition, setActive };
})();
