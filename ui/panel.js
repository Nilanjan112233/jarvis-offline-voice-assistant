// Jarvis panel. Talks to Python through window.pywebview.api; Python pushes events via window.onEvent(evt).
// Without pywebview (opened in a normal browser) it runs a DEMO so the design can be previewed.
const $ = (s) => document.querySelector(s);
const feed = $("#feed"), input = $("#input"), app = $("#app"), statusEl = $("#status");
let api = null, current = null, settings = { voice: true, wake: true, followup: true, autostart: false };

const STATE_LABEL = { idle: "Ready", listening: "Listening…", understanding: "Understanding…", thinking: "Thinking…",
                      working: "Working…", speaking: "Speaking…", waiting: "Waiting for your OK", starting: "Starting up…" };

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
function scroll() { feed.scrollTop = feed.scrollHeight; }
function toast(text) { const t = $("#toast"); t.textContent = text; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3500); }

// living orbs (header + start screen), same renderer as the floating orb
const orbs = [createOrb($("#miniOrb"), { particles: 90, radius: 0.27 })];
if ($("#heroOrb")) orbs.push(createOrb($("#heroOrb"), { particles: 260, radius: 0.25 }));

function applyTheme(name) {
  document.documentElement.dataset.theme = name || "glass";
  orbs.forEach((o) => o.theme(name));
  document.querySelectorAll(".swatch").forEach((s) => s.classList.toggle("on", s.dataset.themePick === name));
}

function setState(state, detail) {
  statusEl.textContent = detail || STATE_LABEL[state] || state;
  orbs.forEach((o) => o.set(state));
  app.classList.toggle("busy", !["idle", "speaking"].includes(state));
  $("#micBtn").classList.toggle("rec", state === "listening");
  if (state !== "listening") $("#micBtn").style.setProperty("--lvl", 0);
}

function setLevel(v) {
  orbs.forEach((o) => o.level(v));
  $("#micBtn").style.setProperty("--lvl", v.toFixed(2));
}

// live words while you talk
function showCaption(text) {
  let c = document.getElementById("liveCap");
  if (!text) { c?.remove(); return; }
  if (!c) { hideEmpty(); c = document.createElement("div"); c.id = "liveCap"; c.className = "msg-you live"; feed.appendChild(c); }
  c.textContent = text; scroll();
}

// voice training (Settings -> Teach Jarvis my voice)
function showTrain(e) {
  const box = $("#train"); box.hidden = false;
  $("#tStep").textContent = e.done ? "" : `${e.step} / ${e.total}`;
  $("#tPrompt").textContent = e.prompt || "";
  $("#tHint").textContent = e.hint || "";
  $("#tBar").style.width = e.done ? "100%" : `${Math.round(100 * (e.step - 1) / e.total)}%`;
  box.classList.toggle("ok", !!e.done && !!e.ok);
  $("#trainBtn").disabled = !e.done;
}

function hideEmpty() { const e = $("#empty"); if (e) { e.remove(); orbs.length = 1; } }

function addYou(text) {
  hideEmpty();
  const d = document.createElement("div");
  d.className = "msg-you"; d.textContent = text;
  feed.appendChild(d);
  current = document.createElement("div");
  current.className = "msg-j";
  current.innerHTML = `<div class="steps"></div><div class="thinking"><i></i><i></i><i></i></div>`;
  feed.appendChild(current); scroll();
}

function addStep(line) {
  if (!current) return;
  const steps = current.querySelector(".steps");
  steps.querySelectorAll(".step.live").forEach((s) => { s.classList.remove("live"); s.classList.add("done"); });
  const s = document.createElement("div");
  s.className = "step live"; s.innerHTML = `<span class="dot"></span><span>${esc(line)}</span>`;
  steps.appendChild(s); scroll();
}

function addAnswer(text, cards, secs, tools) {
  if (!current) { addYou("…"); }
  current.querySelector(".thinking")?.remove();
  current.querySelector(".bubble.draft")?.remove();
  current.querySelectorAll(".step.live").forEach((s) => { s.classList.remove("live"); s.classList.add("done"); });
  const b = document.createElement("div");
  b.className = "bubble"; b.textContent = text;
  current.appendChild(b);
  (cards || []).forEach((c) => { const el = renderCard(c); if (el) current.appendChild(el); });
  const m = document.createElement("div");
  m.className = "meta";
  m.textContent = (tools && tools.length ? `${tools.length} step${tools.length > 1 ? "s" : ""} · ` : "") + `${secs.toFixed(1)}s`;
  current.appendChild(m);
  current = null; scroll();
}

function addAsk(e) {
  hideEmpty();
  const d = document.createElement("div");
  d.className = "ask";
  const details = Object.entries(e.args || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
  d.innerHTML = `<div class="t">⚠️ Allow ${esc(e.tool.replace(/_/g, " "))}?</div>
    <div class="d">${esc(details || "(no details)")}</div><div class="r">${esc(e.reason)}</div>
    <div class="btns"><button class="btn yes">Allow</button><button class="btn no">Deny</button></div>`;
  const answer = (ok) => { d.classList.add("done"); d.querySelector(".t").textContent = (ok ? "✅ Allowed: " : "⛔ Denied: ") + e.tool.replace(/_/g, " "); api.answer_confirm(e.id, ok); };
  d.querySelector(".yes").onclick = () => answer(true);
  d.querySelector(".no").onclick = () => answer(false);
  current?.querySelector(".thinking")?.remove();
  (current || feed).appendChild(d);
  requestAnimationFrame(scroll);
}

// ------------------------------------------------------------ result cards
const WX_ICON = (d) => /thunder/i.test(d) ? "⛈️" : /rain|drizzle|shower/i.test(d) ? "🌧️" : /snow/i.test(d) ? "❄️" :
  /cloud|overcast/i.test(d) ? "☁️" : /haze|mist|fog|smoke/i.test(d) ? "🌥️" : /partly/i.test(d) ? "⛅" : "☀️";

function renderCard(c) {
  const d = document.createElement("div"); d.className = "card";
  if (c.type === "weather") {
    d.innerHTML = `<h3>${esc(c.place)}</h3><div class="wx"><div class="icon">${WX_ICON(c.desc)}</div>
      <div><div class="temp">${esc(c.temp)}°</div></div>
      <div><div class="desc">${esc(c.desc)}</div><div class="sub">Feels ${esc(c.feels)}° · Humidity ${esc(c.humidity)}% · Wind ${esc(c.wind)} km/h</div>
      <div class="sub">Today ${esc(c.today)} · Tomorrow ${esc(c.tomorrow)}${c.rain ? ` · Rain ${esc(c.rain)}%` : ""}</div></div></div>`;
  } else if (c.type === "system") {
    const disks = (c.disks || []).map((k) => `<div class="stat"><div class="k">${esc(k.name)} free</div><div class="v">${k.free} GB</div>
      <div class="bar ${k.used_pct > 88 ? "warn" : ""}"><b style="width:${k.used_pct}%"></b></div></div>`).join("");
    d.innerHTML = `<h3>This PC</h3><div class="grid2">
      ${c.battery != null ? `<div class="stat"><div class="k">Battery ${c.charging ? "⚡" : ""}</div><div class="v">${c.battery}%</div>
        <div class="bar ${c.battery < 20 ? "warn" : ""}"><b style="width:${c.battery}%"></b></div></div>` : ""}
      <div class="stat"><div class="k">RAM free</div><div class="v">${c.ram_free} / ${c.ram_total} GB</div>
        <div class="bar ${c.ram_used_pct > 88 ? "warn" : ""}"><b style="width:${c.ram_used_pct}%"></b></div></div>${disks}</div>`;
  } else if (c.type === "list") {
    d.innerHTML = `<h3>${esc(c.title)}</h3><div class="list">${(c.items || []).slice(0, 8).map((it, i) =>
      `<div class="row"><span class="n">${i + 1}</span><span>${esc(it.text)}</span>${it.side ? `<span class="s">${esc(it.side)}</span>` : ""}</div>`).join("")}</div>`;
  } else if (c.type === "big") {
    d.innerHTML = `<h3>${esc(c.title)}</h3><div class="bignum">${esc(c.value)}</div>${c.sub ? `<div class="sub">${esc(c.sub)}</div>` : ""}`;
  } else if (c.type === "pill") {
    const p = document.createElement("div"); p.className = "pill"; p.textContent = c.text; return p;
  } else return null;
  return d;
}

// ------------------------------------------------------------ events from Python
window.onEvent = function (e) {
  switch (e.type) {
    case "state": setState(e.state, e.detail); break;
    case "heard": input.value = ""; showCaption(""); if (!pendingTyped || pendingTyped !== e.text) addYou(e.text); pendingTyped = null; break;
    case "step": addStep(e.line); break;
    case "confirm": addAsk(e); break;
    case "answer": addAnswer(e.text, e.cards, e.secs, e.tools); break;
    case "error": addAnswer("⚠️ " + e.text, [], 0, []); break;
    case "reminder": hideEmpty(); { const d = document.createElement("div"); d.className = "msg-j";
      d.innerHTML = `<div class="ask"><div class="t">⏰ Reminder</div><div class="r" style="color:var(--text);font-size:14px">${esc(e.text)}</div></div>`;
      feed.appendChild(d); scroll(); } break;
    case "toast": toast(e.text); break;
    case "caption": showCaption(e.text); break;
    case "draft": showDraft(e.text); break;
    case "queued": addQueued(e.text, e.n); break;
    case "begin": { const q = feed.querySelector(".msg-you.queued"); if (q) q.remove(); } break;
    case "unqueue": feed.querySelectorAll(".msg-you.queued").forEach((q) => q.remove()); break;
    case "level": setLevel(e.v); break;
    case "train": showTrain(e); break;
    case "settings": settings = e.settings; applyTheme(settings.theme); renderSettings(e); break;
  }
};

// ------------------------------------------------------------ user actions
let pendingTyped = null;
function send(text) {
  text = (text ?? input.value).trim();
  if (!text) return;
  input.value = "";
  if (!app.classList.contains("busy") && !/^\W*(stop|cancel)\b/i.test(text)) { addYou(text); pendingTyped = text; }
  api.send(text);        // busy -> Python queues it (or stops on "stop")
}

// the reply being written, word by word
function showDraft(text) {
  if (!current) return;
  let d = current.querySelector(".bubble.draft");
  if (!text) { d?.remove(); return; }
  current.querySelector(".thinking")?.remove();
  if (!d) { d = document.createElement("div"); d.className = "bubble draft"; current.appendChild(d); }
  d.textContent = text; scroll();
}

function addQueued(text, n) {
  hideEmpty();
  const d = document.createElement("div");
  d.className = "msg-you queued"; d.textContent = text; d.title = "Queued — Jarvis will do this next";
  feed.appendChild(d); scroll();
  toast(n > 1 ? `Queued (${n} waiting). Say or type "stop" to cancel.` : 'Queued — I\'ll do that next. Say "stop" to cancel.');
}
$("#sendBtn").onclick = () => send();
input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
$("#micBtn").onclick = () => api.listen();
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.code === "Space") { e.preventDefault(); api.listen(); }
  if (e.key === "Escape") api.hush();
});
$("#chips").onclick = (e) => { const q = e.target.closest(".chip")?.dataset.q; if (q) send(q); };
$("#hideBtn").onclick = () => api.hide_panel();
$("#settingsBtn").onclick = () => { api.get_settings(); $("#sheet").classList.add("open"); };
$("#backBtn").onclick = () => $("#sheet").classList.remove("open");
$("#openData").onclick = () => api.open_data();
$("#quitBtn").onclick = () => api.quit();
document.querySelectorAll(".swatch").forEach((s) => (s.onclick = () => { applyTheme(s.dataset.themePick); api.set_setting("theme", s.dataset.themePick); }));
$("#trainBtn").onclick = () => { $("#trainBtn").disabled = true; api.train_voice(); };
$("#voiceBtn").onclick = () => api.set_setting("voice", !settings.voice);
document.querySelectorAll(".toggle").forEach((t) => (t.onclick = () => api.set_setting(t.dataset.set, !settings[t.dataset.set])));

document.querySelectorAll("#speedList .vpick").forEach((b) => (b.onclick = () => api.set_setting("speech_speed", +b.dataset.speed)));

function renderSettings(e) {
  const vl = $("#voiceList");
  if (vl && e.voices) {
    vl.innerHTML = Object.entries(e.voices).map(([k, v]) => `<button class="vpick${k === e.current_voice ? " on" : ""}" data-v="${esc(k)}">${esc(v)}</button>`).join("");
    vl.querySelectorAll(".vpick").forEach((b) => (b.onclick = () => { vl.querySelectorAll(".vpick").forEach((x) => x.classList.remove("on")); b.classList.add("on"); b.textContent += " …"; api.set_setting("speaker_voice", b.dataset.v); }));
  }
  const sp = +(settings.speech_speed || 1);
  document.querySelectorAll("#speedList .vpick").forEach((b) => b.classList.toggle("on", Math.abs(+b.dataset.speed - sp) < 0.08));
  document.querySelectorAll(".toggle").forEach((t) => t.classList.toggle("on", !!settings[t.dataset.set]));
  $("#voiceBtn").classList.toggle("on", !!settings.voice);
  $("#voiceState").textContent = e.voice_trained ? "Trained on your voice ✓ — run again any time to refresh it."
    : 'Say "Hey Jarvis" 10 times + read 4 lines (~1 min). Wakes more reliably, ignores other voices.';
  $("#trainBtn").textContent = e.voice_trained ? "Retrain" : "Start";
  const mem = $("#memList"), sk = $("#skillList");
  mem.innerHTML = (e.memory || []).length ? e.memory.map((m, i) => `<div class="item"><span>${esc(m)}</span><button data-mem="${i}" title="Forget">×</button></div>`).join("")
    : `<div class="muted">Nothing yet. Tell Jarvis things like "my name is…" or "I live in…".</div>`;
  sk.innerHTML = (e.skills || []).length ? e.skills.map((s) => `<div class="item"><span>${esc(s)}</span><button data-skill="${esc(s.split(":")[0])}" title="Delete">×</button></div>`).join("")
    : `<div class="muted">No skills yet. Say "learn this skill: when I say …, do …".</div>`;
  mem.querySelectorAll("button").forEach((b) => (b.onclick = () => api.forget_index(+b.dataset.mem)));
  sk.querySelectorAll("button").forEach((b) => (b.onclick = () => api.delete_skill(b.dataset.skill)));
}

const h = new Date().getHours();
$("#greet").textContent = h < 12 ? "Good morning." : h < 17 ? "Good afternoon." : "Good evening.";

// ------------------------------------------------------------ connect (or demo)
function connect() {
  if (window.pywebview && window.pywebview.api) { api = window.pywebview.api; api.ready(); return; }
  setTimeout(() => { if (!api) startDemo(); }, 1200);
}
window.addEventListener("pywebviewready", () => { api = window.pywebview.api; api.ready(); });
connect();

function startDemo() {
  const fire = (e, t) => setTimeout(() => window.onEvent(e), t);
  api = {
    send(text) {
      fire({ type: "state", state: "thinking" }, 50);
      if (/weather/i.test(text)) {
        fire({ type: "step", line: "Checking the weather in Kolkata" }, 500);
        fire({ type: "answer", text: "It's 27 degrees and hazy in Kolkata right now. Tomorrow looks similar, 21 to 30, with a small chance of rain.",
          secs: 3.2, tools: ["get_weather"], cards: [{ type: "weather", place: "Kolkata", desc: "Smoky haze", temp: 27, feels: 29, humidity: 60, wind: 6, today: "20–29°", tomorrow: "21–30°", rain: 12 }] }, 1800);
      } else if (/system/i.test(text)) {
        fire({ type: "step", line: "Checking your PC" }, 400);
        fire({ type: "answer", text: "All good. Battery at 82 percent and charging, 6.3 GB of RAM free, and 27.6 GB free on C.", secs: 0.4, tools: ["system_info"],
          cards: [{ type: "system", battery: 82, charging: true, ram_free: 6.3, ram_total: 17, ram_used_pct: 63, disks: [{ name: "C:", free: 27.6, used_pct: 86 }, { name: "D:", free: 46.6, used_pct: 85 }] }] }, 900);
      } else if (/delete/i.test(text)) {
        fire({ type: "state", state: "waiting" }, 400);
        fire({ type: "confirm", id: 1, tool: "delete_file", args: { path: "D:/Downloads/old_setup.exe" }, reason: "deletes (to the Recycle Bin): D:\\Downloads\\old_setup.exe" }, 600);
      } else {
        fire({ type: "step", line: "Checking the news" }, 400);
        fire({ type: "answer", text: "Here are today's top stories.", secs: 4.1, tools: ["get_news"],
          cards: [{ type: "list", title: "Top headlines", items: [{ text: "US assures India of a tariff advantage", side: "The Hindu" }, { text: "Delhi air quality worst in the world today", side: "Reuters" }, { text: "India U19 reach Asia Cup final", side: "BCCI" }] }] }, 1500);
      }
      fire({ type: "state", state: "idle" }, 2000);
    },
    answer_confirm(id, ok) { window.onEvent({ type: "answer", text: ok ? "Done. It's in the Recycle Bin if you change your mind." : "Alright, I've left it.", secs: 2.0, tools: ["delete_file"], cards: [] }); setState("idle"); },
    listen() { setState("listening"); setTimeout(() => { setState("idle"); window.onEvent({ type: "heard", text: "What's the weather like?" }); api.send("weather"); }, 1500); },
    hush() {}, quit() {}, hide_panel() { toast("The panel hides; the orb stays on screen."); }, open_data() {},
    get_settings() { window.onEvent({ type: "settings", settings, memory: ["User's name is Alex", "User lives in Kolkata", "Don't use Spotify"], skills: ["system_check: when I say 'system check'"] }); },
    set_setting(k, v) { settings[k] = v; this.get_settings(); }, train_voice() {}, forget_voice() {}, forget_index() {}, delete_skill() {}, ready() {},
  };
  setState("idle", "Demo mode (no backend)");
  if (location.hash === "#crimson") applyTheme("crimson");
}
