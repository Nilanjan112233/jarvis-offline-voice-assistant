// Jarvis orb: a living sphere of particles. Same renderer for the floating orb, the panel header and the start screen.
//   idle       slow drift, breathing
//   listening  surface ripples with your voice (mic level)
//   understanding / thinking   particles pull into spinning bands
//   working    bands + a progress arc running round the sphere
//   speaking   pulses outward like a voice
//   waiting    gold, slow heartbeat (needs your OK)
//   starting   dim, assembling
const THEMES = {
  glass:   { a: [205, 230, 255], b: [115, 140, 180], c: [255, 255, 255], warn: [255, 205, 100], core: [10, 14, 22] },
  emerald: { a: [46, 255, 170], b: [10, 190, 120], c: [190, 255, 225], warn: [240, 200, 100], core: [4, 40, 26] },
  crimson: { a: [255, 72, 60],  b: [210, 20, 50],  c: [255, 190, 150], warn: [255, 196, 90], core: [40, 6, 8] },
};

function createOrb(canvas, opts = {}) {
  const ctx = canvas.getContext("2d");
  const N = opts.particles || 240;
  const S = { state: "starting", level: 0, theme: opts.theme || "glass", t: 0, lvl: 0, energy: 0, bands: 0, gold: 0, dim: 1 };
  // fibonacci sphere
  const pts = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963;
    pts.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, seed: Math.random() * 6.28, lat: Math.asin(y) });
  }
  let col = THEMES[S.theme].a.slice(), col2 = THEMES[S.theme].b.slice();
  const mix = (a, b, k) => a.map((v, i) => v + (b[i] - v) * k);
  const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
  const ease = (cur, target, k) => cur + (target - cur) * k;
  let frame = 0, last = performance.now();

  function draw(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    frame++;
    const calm = S.state === "idle" || S.state === "starting";
    if (calm && opts.saveCpu && frame % 2) { requestAnimationFrame(draw); return; }   // idle: 30 fps
    S.t += dt;
    const st = S.state, T = THEMES[S.theme] || THEMES.emerald;
    // smooth state parameters
    S.lvl = ease(S.lvl, st === "listening" ? Math.min(1, S.level * 1.6) : 0, 0.25);
    S.energy = ease(S.energy, { listening: .5, understanding: .7, thinking: .9, working: 1, speaking: .8, waiting: .3, starting: .1 }[st] ?? .2, 0.05);
    S.bands = ease(S.bands, ["thinking", "working", "understanding"].includes(st) ? 1 : 0, 0.04);
    S.gold = ease(S.gold, st === "waiting" ? 1 : 0, 0.06);
    S.dim = ease(S.dim, st === "starting" ? 0.45 : 1, 0.04);
    const target = mix(st === "speaking" ? T.c : T.a, T.warn, S.gold);
    col = mix(col, target, 0.08); col2 = mix(col2, mix(T.b, T.warn, S.gold * .7), 0.08);

    const W = canvas.width, C = W / 2, R0 = W * (opts.radius || 0.3);
    ctx.clearRect(0, 0, W, W);
    const breathe = .025 * Math.sin(S.t * 1.7);
    const speak = st === "speaking" ? .07 * Math.abs(Math.sin(S.t * 7.3)) + .04 * Math.abs(Math.sin(S.t * 11.1)) : 0;
    const beat = st === "waiting" ? .05 * Math.max(0, Math.sin(S.t * 4)) ** 8 : 0;
    const R = R0 * (1 + breathe + speak + beat + S.lvl * .1);

    // halo
    let g = ctx.createRadialGradient(C, C, R * .2, C, C, R * 1.9);
    g.addColorStop(0, rgba(col, .30 * S.dim)); g.addColorStop(.55, rgba(col2, .10 * S.dim)); g.addColorStop(1, rgba(col2, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(C, C, R * 1.9, 0, 6.3); ctx.fill();
    // core
    g = ctx.createRadialGradient(C - R * .2, C - R * .25, 0, C, C, R * .95);
    g.addColorStop(0, rgba(mix(col, [255, 255, 255], .5), .55 * S.dim)); g.addColorStop(.45, rgba(col2, .22 * S.dim)); g.addColorStop(1, rgba(T.core, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(C, C, R, 0, 6.3); ctx.fill();

    // particles
    const rotY = S.t * (.25 + S.energy * 1.2), rotX = .35 + .15 * Math.sin(S.t * .3);
    const cy = Math.cos(rotY), sy = Math.sin(rotY), cx = Math.cos(rotX), sx = Math.sin(rotX);
    const out = [];
    for (const p of pts) {
      let { x, y, z } = p;
      // bands: pull each particle toward one of 5 latitude rings (thinking)
      if (S.bands > .01) {
        const ring = Math.round(p.lat / 0.55) * 0.55, ny = Math.sin(ring), nr = Math.cos(ring), a = Math.atan2(z, x) + S.t * (1 + (Math.round(p.lat / .55) % 2 ? -.8 : .8));
        x = x + (Math.cos(a) * nr - x) * S.bands; y = y + (ny - y) * S.bands; z = z + (Math.sin(a) * nr - z) * S.bands;
      }
      // ripple from the voice / speaking pulse
      const wob = 1 + S.lvl * .22 * Math.sin(p.seed * 3 + S.t * 14 + y * 6) + .03 * Math.sin(p.seed + S.t * 2)
                + (st === "speaking" ? .06 * Math.sin(S.t * 9 - y * 5) : 0) + (st === "starting" ? .4 * Math.max(0, 1 - S.t / 1.5) * Math.sin(p.seed) : 0);
      x *= wob; y *= wob; z *= wob;
      const x1 = x * cy - z * sy, z1 = x * sy + z * cy;
      const y2 = y * cx - z1 * sx, z2 = y * sx + z1 * cx;
      out.push([C + x1 * R, C + y2 * R, z2]);
    }
    out.sort((a, b) => a[2] - b[2]);
    for (const [px, py, z] of out) {
      const d = (z + 1) / 2;                       // 0 back .. 1 front
      const size = (W / 240) * (0.7 + d * 1.9);
      ctx.fillStyle = rgba(mix(col2, col, d), (0.15 + d * .85) * S.dim);
      ctx.beginPath(); ctx.arc(px, py, size, 0, 6.3); ctx.fill();
    }

    // working: progress arc; listening: level ring
    if (st === "working" || S.bands > .5) {
      ctx.strokeStyle = rgba(col, .8); ctx.lineWidth = W / 110; ctx.lineCap = "round";
      const a0 = S.t * 3.2;
      ctx.beginPath(); ctx.arc(C, C, R * 1.32, a0, a0 + 1.2 + .6 * Math.sin(S.t * 2)); ctx.stroke();
    }
    ctx.strokeStyle = rgba(col, .18 + S.lvl * .6); ctx.lineWidth = W / 200;
    ctx.beginPath(); ctx.arc(C, C, R * (1.32 + S.lvl * .12), 0, 6.3); ctx.stroke();
    // tick marks (slowly turning)
    ctx.save(); ctx.translate(C, C); ctx.rotate(-S.t * .15);
    ctx.strokeStyle = rgba(col2, .35 * S.dim); ctx.lineWidth = W / 240;
    for (let i = 0; i < 48; i++) {
      const a = i / 48 * 6.283, r1 = R * 1.45, r2 = r1 + (i % 4 ? W * .012 : W * .03);
      ctx.beginPath(); ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1); ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2); ctx.stroke();
    }
    ctx.restore();
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
  return {
    set(state, level) { if (state) S.state = state; if (level != null) S.level = level; },
    level(v) { S.level = v; },
    theme(name) { if (THEMES[name]) S.theme = name; },
  };
}
window.createOrb = createOrb;
