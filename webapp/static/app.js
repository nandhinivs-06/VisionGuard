/* VisionGuard dashboard - front end (vanilla JS, no external libraries) */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => (v || v === 0) && v > 0 ? `${Math.round(v * 100)}%` : "–";

/* localStorage can be blocked (private mode, strict settings): never let it break the page */
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* ignore */ } },
};

const state = {
  srcType: "dataset",
  running: false,
  status: null,
  seenAlerts: new Set(),
  uploadedPath: null,
  incidents: [],
  incFilter: "all",
  incReview: "all",
  lastIncidentCount: 0,
};
const prefs = {
  theme: store.get("vg-theme", "dark"),          // dark | light | auto
  accent: store.get("vg-accent", "blue"),
  sound: store.get("vg-sound", "0") === "1",
  volume: +store.get("vg-volume", "0.5"),
  notify: store.get("vg-notify", "0") === "1",
  alarm: store.get("vg-alarm", "1") === "1",     // full-screen alarm on/off
  snooze: +store.get("vg-snooze", "60"),         // seconds
};
function savePrefs() {
  store.set("vg-theme", prefs.theme); store.set("vg-accent", prefs.accent);
  store.set("vg-sound", prefs.sound ? "1" : "0"); store.set("vg-volume", String(prefs.volume));
  store.set("vg-notify", prefs.notify ? "1" : "0"); store.set("vg-alarm", prefs.alarm ? "1" : "0");
  store.set("vg-snooze", String(prefs.snooze));
}

/* ------------------------------------------------------------------ helpers */
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.innerHTML = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 4200);
  setTimeout(() => t.remove(), 4700);
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
}

/* ------------------------------------------------------------------ clock + routing */
setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); }, 1000);

function route() {
  const v = (location.hash || "#live").slice(1);
  $$(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${v}`));
  $$(".tab").forEach((el) => el.classList.toggle("active", el.dataset.view === v));
  if (v === "incidents") loadIncidents();
  if (v === "analytics") loadAnalytics();
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ theme + preferences */
const darkMq = window.matchMedia("(prefers-color-scheme: light)");
function applyTheme() {
  const t = prefs.theme === "auto" ? (darkMq.matches ? "light" : "dark") : prefs.theme;
  document.documentElement.dataset.theme = t;
  document.documentElement.dataset.accent = prefs.accent;
  $$("#themeOpts .theme-opt").forEach((b) => b.classList.toggle("active", b.dataset.themeOpt === prefs.theme));
  $$("#accentOpts .accent-dot").forEach((b) => b.classList.toggle("active", b.dataset.accentOpt === prefs.accent));
  if (state.status) drawChart(state.status.trace, +$("#conf").value);
}
darkMq.addEventListener?.("change", () => prefs.theme === "auto" && applyTheme());

function renderPrefs() {
  $("#prefSound").checked = prefs.sound;
  $("#prefVolume").value = prefs.volume;
  $("#prefNotify").checked = prefs.notify && "Notification" in window && Notification.permission === "granted";
  $("#prefAlarm").checked = prefs.alarm;
  $("#prefSnooze").value = String(prefs.snooze);
  $("#soundBtn").setAttribute("aria-pressed", prefs.sound ? "true" : "false");
}

$("#prefsBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const p = $("#prefs");
  p.hidden = !p.hidden;
  $("#prefsBtn").setAttribute("aria-expanded", String(!p.hidden));
});
document.addEventListener("click", (e) => {
  if (!$("#prefs").hidden && !e.target.closest(".prefs-wrap")) { $("#prefs").hidden = true; $("#prefsBtn").setAttribute("aria-expanded", "false"); }
});
$$("#themeOpts .theme-opt").forEach((b) => b.addEventListener("click", () => { prefs.theme = b.dataset.themeOpt; savePrefs(); applyTheme(); }));
$$("#accentOpts .accent-dot").forEach((b) => b.addEventListener("click", () => { prefs.accent = b.dataset.accentOpt; savePrefs(); applyTheme(); }));
$("#prefSound").addEventListener("change", (e) => setSound(e.target.checked));
$("#prefVolume").addEventListener("input", (e) => { prefs.volume = +e.target.value; savePrefs(); });
$("#prefVolume").addEventListener("change", () => { if (prefs.sound) beep(); });
$("#prefAlarm").addEventListener("change", (e) => { prefs.alarm = e.target.checked; savePrefs(); });
$("#prefSnooze").addEventListener("change", (e) => { prefs.snooze = +e.target.value; savePrefs(); });
$("#prefNotify").addEventListener("change", async (e) => {
  if (!e.target.checked) { prefs.notify = false; savePrefs(); return; }
  if (!("Notification" in window)) { toast("This browser does not support desktop notifications", "err"); e.target.checked = false; return; }
  const perm = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  prefs.notify = perm === "granted";
  e.target.checked = prefs.notify;
  savePrefs();
  toast(prefs.notify ? "🔔 Desktop notifications <b>on</b>" : "Notifications were blocked by the browser", prefs.notify ? "" : "err");
});

function toggleTheme() {
  const current = document.documentElement.dataset.theme;
  prefs.theme = current === "dark" ? "light" : "dark";
  savePrefs(); applyTheme();
  toast(`Theme: <b>${prefs.theme}</b>`);
}

/* desktop notification + flashing tab title / icon while the page is in the background */
const baseTitle = document.title;
const baseIcon = $("link[rel=icon]").href;
const alarmIcon = "data:image/svg+xml," + encodeURIComponent("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><circle cx='12' cy='12' r='11' fill='#ff2d1a'/><text x='12' y='17' font-size='14' text-anchor='middle' fill='white' font-family='Arial' font-weight='bold'>!</text></svg>");
let flashTimer = null;
function notifyBackground(a) {
  if (prefs.notify && "Notification" in window && Notification.permission === "granted" && document.hidden) {
    const n = new Notification(`🚨 ${a.type.toUpperCase()} DETECTED`, {
      body: `${a.camera} · confidence ${pct(a.confidence)} · ${a.time} · severity ${a.severity}`,
      icon: a.snapshot || undefined, tag: `vg-${a.type}`, requireInteraction: a.type === "fire",
    });
    n.onclick = () => { window.focus(); location.hash = "#live"; n.close(); };
  }
  if (document.hidden && !flashTimer) {
    let on = false;
    flashTimer = setInterval(() => {
      on = !on;
      document.title = on ? `🔥 ${a.type.toUpperCase()}! · VisionGuard` : baseTitle;
      $("link[rel=icon]").href = on ? alarmIcon : baseIcon;
    }, 800);
  }
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && flashTimer) {
    clearInterval(flashTimer); flashTimer = null;
    document.title = baseTitle; $("link[rel=icon]").href = baseIcon;
  }
});

/* ------------------------------------------------------------------ sound (Web Audio siren) */
let audioCtx = null;
function beep() {
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  const o = audioCtx.createOscillator(), g = audioCtx.createGain();
  o.frequency.value = 880; o.connect(g); g.connect(audioCtx.destination);
  const t0 = audioCtx.currentTime, v = 0.3 * prefs.volume;
  g.gain.setValueAtTime(v, t0); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.25);
  o.start(t0); o.stop(t0 + 0.26);
}
function setSound(on) {
  prefs.sound = on; savePrefs(); renderPrefs();
  if (on) { beep(); toast("🔔 Alarm sound <b>on</b>"); } else toast("🔕 Alarm sound off");
}
function siren(kind) {
  if (!prefs.sound) return;
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  const o = audioCtx.createOscillator(), g = audioCtx.createGain();
  o.type = "sawtooth";
  o.connect(g); g.connect(audioCtx.destination);
  const t0 = audioCtx.currentTime, hi = kind === "fire" ? 980 : 720, lo = kind === "fire" ? 620 : 480;
  for (let i = 0; i < 6; i++) {
    o.frequency.setValueAtTime(hi, t0 + i * 0.5);
    o.frequency.linearRampToValueAtTime(lo, t0 + i * 0.5 + 0.45);
  }
  const vol = Math.max(0.0002, 0.36 * prefs.volume);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(vol, t0 + 0.05);
  g.gain.setValueAtTime(vol, t0 + 2.9);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + 3);
  o.start(t0); o.stop(t0 + 3.05);
}
$("#soundBtn").addEventListener("click", () => setSound(!prefs.sound));
applyTheme();
renderPrefs();

/* ------------------------------------------------------------------ source controls */
$$("#srcTabs .seg-btn").forEach((b) => b.addEventListener("click", () => {
  state.srcType = b.dataset.src;
  $$("#srcTabs .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  $$(".src-pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === state.srcType));
}));

function bindSlider(id, out, fmt = (v) => v) {
  const el = $(id), o = $(out);
  const upd = () => { o.textContent = fmt(el.value); };
  el.addEventListener("input", upd);
  return upd;
}
const updConf = bindSlider("#conf", "#confVal", (v) => Number(v).toFixed(2));
const updFps = bindSlider("#sampleFps", "#fpsVal", (v) => `${v} fps`);
const updWin = bindSlider("#window", "#winVal", (v) => `${v} frames`);
const updHits = bindSlider("#minHits", "#hitsVal", (v) => `${v} / ${$("#window").value}`);
$("#window").addEventListener("input", () => {
  $("#minHits").max = $("#window").value;
  if (+$("#minHits").value > +$("#window").value) $("#minHits").value = $("#window").value;
  updHits();
});

async function loadSources() {
  const d = await api("/api/sources");
  const sel = $("#videoSelect");
  sel.innerHTML = "";
  const groups = {};
  d.videos.forEach((v) => { (groups[v.split] = groups[v.split] || []).push(v); });
  for (const split of ["test", "val", "train", "uploaded"]) {
    if (!groups[split]) continue;
    const og = document.createElement("optgroup");
    og.label = { test: "Test videos (unseen)", val: "Validation videos", train: "Training videos", uploaded: "Uploaded" }[split];
    groups[split].forEach((v) => {
      const o = document.createElement("option");
      o.value = v.path;
      o.textContent = `${v.label === "fire/smoke" ? "🔥" : v.label === "no fire" ? "✓" : "⬆"}  ${v.name.replace("__", " · ")}`;
      o.dataset.label = v.label; o.dataset.dataset = v.dataset;
      og.appendChild(o);
    });
    sel.appendChild(og);
  }
  if (!d.videos.length) sel.innerHTML = "<option value=''>No dataset videos – run dataset_prep.run_all</option>";
  const showHint = () => {
    const o = sel.selectedOptions[0];
    $("#videoHint").textContent = o && o.dataset.label ? `Ground truth: ${o.dataset.label} · dataset: ${o.dataset.dataset}` : "";
  };
  sel.onchange = showHint; showHint();

  const ms = $("#modelSelect");
  ms.innerHTML = d.models.map((m) => `<option value="${esc(m.path)}" ${m.path === d.defaults.model ? "selected" : ""}>${esc(m.name)}</option>`).join("");
  $("#conf").value = d.defaults.conf; $("#sampleFps").value = d.defaults.sample_fps;
  $("#window").value = d.defaults.window; $("#minHits").max = d.defaults.window; $("#minHits").value = d.defaults.min_hits;
  updConf(); updFps(); updWin(); updHits();
}

/* upload */
const drop = $("#drop");
["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("drag"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("drag"); }));
drop.addEventListener("drop", (ev) => ev.dataTransfer.files[0] && uploadFile(ev.dataTransfer.files[0]));
$("#fileInput").addEventListener("change", (ev) => ev.target.files[0] && uploadFile(ev.target.files[0]));

function uploadFile(file) {
  const bar = $("#uploadProgress"), fill = bar.firstElementChild;
  bar.style.display = "block"; fill.style.width = "0";
  $("#dropText").textContent = `Uploading ${file.name}…`;
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/upload?name=${encodeURIComponent(file.name)}`);
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) fill.style.width = `${(e.loaded / e.total) * 100}%`; };
  xhr.onload = () => {
    if (xhr.status === 200) {
      const r = JSON.parse(xhr.responseText);
      state.uploadedPath = r.path;
      $("#dropText").innerHTML = `✓ <b>${esc(r.name)}</b> ready. Press Start`;
      toast(`Uploaded <b>${esc(r.name)}</b>`);
      loadSources();
    } else {
      $("#dropText").textContent = "Upload failed. Try again";
      toast(`Upload failed: ${esc(xhr.responseText)}`, "err");
    }
    setTimeout(() => { bar.style.display = "none"; }, 800);
  };
  xhr.send(file);
}

function currentSource() {
  switch (state.srcType) {
    case "dataset": return $("#videoSelect").value;
    case "upload": return state.uploadedPath;
    case "webcam": return String($("#webcamIndex").value || 0);
    case "rtsp": return $("#rtspUrl").value.trim();
  }
  return null;
}

$("#startBtn").addEventListener("click", async () => {
  const source = currentSource();
  if (!source) { toast(state.srcType === "upload" ? "Upload a video first" : "Choose a video source", "err"); return; }
  $("#startBtn").disabled = true;
  try {
    await api("/api/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source, camera: $("#cameraName").value.trim() || "Camera 1", model: $("#modelSelect").value,
        conf: +$("#conf").value, sample_fps: +$("#sampleFps").value, window: +$("#window").value,
        min_hits: +$("#minHits").value, realtime: $("#realtime").checked,
        email: !!$("#emailAlerts")?.checked,
      }),
    });
    state.seenAlerts.clear();
    resetChart();
    $("#stream").src = `/api/stream?t=${Date.now()}`;
    toast("▶ Monitoring started");
    if (prefs.sound && audioCtx?.state === "suspended") audioCtx.resume();
  } catch (e) {
    toast(`Could not start: ${esc(e.message)}`, "err");
  } finally {
    $("#startBtn").disabled = false;
  }
});
$("#stopBtn").addEventListener("click", async () => {
  await api("/api/stop", { method: "POST" }).catch(() => {});
  toast("■ Monitoring stopped");
});

/* ------------------------------------------------------------------ live status (SSE) */
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onopen = () => { $("#conn").className = "conn ok"; $("#connText").textContent = "Connected"; };
  es.onerror = () => { $("#conn").className = "conn bad"; $("#connText").textContent = "Reconnecting…"; };
  es.onmessage = (ev) => {
    const s = JSON.parse(ev.data);
    if (!state.primed) {  // alerts raised before this page was opened: list them, but don't sound the alarm
      s.alerts.forEach((a) => state.seenAlerts.add(`${a.incident_id || ""}-${a.time}-${a.type}`));
      state.primed = true;
    }
    render(s);
  };
}

const BANNERS = {
  IDLE: ["banner-idle", "System idle", "Select a video source and press Start"],
  STARTING: ["banner-idle", "Starting…", "Opening the video source and loading the model"],
  NORMAL: ["banner-ok", "Status: Normal", "No fire or smoke detected"],
  "SMOKE DETECTED": ["banner-smoke", "⚠ ALERT: SMOKE DETECTED", "Smoke confirmed over multiple frames"],
  "FIRE DETECTED": ["banner-fire", "🚨 CRITICAL ALERT: FIRE DETECTED", "Fire confirmed over multiple frames, respond immediately"],
  OFFLINE: ["banner-off", "Camera offline", "Trying to reconnect to the video source…"],
  STOPPED: ["banner-idle", "Monitoring stopped", "Press Start to monitor again"],
};

function render(s) {
  const wasRunning = state.running;
  state.running = s.running; state.status = s;

  // banner
  const key = s.running ? (s.online === false && s.status !== "STARTING" ? "OFFLINE" : s.status) : (s.status === "STOPPED" ? "STOPPED" : "IDLE");
  const [cls, title, sub] = BANNERS[key] || BANNERS.NORMAL;
  $("#banner").className = `banner ${cls}`;
  $("#bannerTitle").textContent = title;
  $("#bannerSub").textContent = s.camera ? `${s.camera} · ${sub}` : sub;
  $("#bannerRight").innerHTML = s.camera ? `${esc(s.source)}<br>events: 🔥 ${s.events.fire} · 💨 ${s.events.smoke}` : "";

  // video
  const wrap = $("#videoWrap");
  wrap.classList.toggle("on", s.running || !!$("#stream").naturalWidth && key === "STOPPED");
  wrap.classList.toggle("alarm-fire", s.status === "FIRE DETECTED" && s.running);
  wrap.classList.toggle("alarm-smoke", s.status === "SMOKE DETECTED" && s.running);
  if (s.running && !wasRunning && !$("#stream").src.includes("/api/stream")) $("#stream").src = `/api/stream?t=${Date.now()}`;
  $("#camPill").textContent = s.camera || "—";
  $("#timePill").textContent = `t = ${(s.video_time || 0).toFixed(1)} s`;
  $("#fpsPill").textContent = `${(s.fps || 0).toFixed(1)} FPS`;
  $("#progressBar").style.width = s.progress != null ? `${s.progress * 100}%` : (s.running ? "100%" : "0");
  renderMarks(s);

  // controls
  $("#runChip").textContent = s.running ? "Running" : "Idle";
  $("#runChip").classList.toggle("run", s.running);
  $("#stopBtn").disabled = !s.running;
  $("#startBtn").innerHTML = `<svg viewBox="0 0 24 24"><path d="M7 5l12 7-12 7z"/></svg>${s.running ? "Restart" : "Start monitoring"}`;

  // KPIs
  const camTxt = s.running ? (s.online === false && s.status !== "STARTING" ? "Offline" : "Online") : "Idle";
  setKpi("#kCamera", camTxt, camTxt === "Online" ? "val-ok" : camTxt === "Offline" ? "val-fire" : "val-dim");
  const stTxt = s.running ? ({ "FIRE DETECTED": "FIRE", "SMOKE DETECTED": "SMOKE", NORMAL: "Normal", STARTING: "Starting" }[s.status] || s.status) : "Normal";
  setKpi("#kStatus", stTxt, stTxt === "FIRE" ? "val-fire" : stTxt === "SMOKE" ? "val-smoke" : "val-ok");
  setKpi("#kFire", s.fire, s.fire ? "val-fire" : "");
  setKpi("#kSmoke", s.smoke, s.smoke ? "val-smoke" : "");
  $("#kFireSub").textContent = `now · ${s.events.fire} event${s.events.fire === 1 ? "" : "s"}`;
  $("#kSmokeSub").textContent = `now · ${s.events.smoke} event${s.events.smoke === 1 ? "" : "s"}`;
  setKpi("#kActive", s.active.length, s.active.length ? "val-fire" : "");
  setKpi("#kConf", pct(s.confidence), "");
  $("#kFire").parentElement.classList.toggle("hot-fire", s.active.includes("fire"));
  $("#kSmoke").parentElement.classList.toggle("hot-smoke", s.active.includes("smoke"));

  // confirmation meters
  renderMeter("#meterFire", "#meterFireVal", s.hits.fire || 0, s.window, s.min_hits, "fire");
  renderMeter("#meterSmoke", "#meterSmokeVal", s.hits.smoke || 0, s.window, s.min_hits, "smoke");
  $("#confirmRule").textContent = `${s.min_hits} of last ${s.window} analysed frames`;
  $("#rejectedText").textContent = s.running ? `Weak detections filtered on the last frame: ${s.rejected}.` : "";

  // chart
  drawChart(s.trace, +$("#conf").value);

  // alerts
  renderAlerts(s.alerts);
  if (s.error && s.error !== state.lastError) { toast(`Error: ${esc(s.error)}`, "err"); }
  state.lastError = s.error;
}

function setKpi(id, value, cls) {
  const el = $(id);
  el.textContent = value;
  el.className = `kpi-value ${cls || ""}`;
}

function renderMeter(id, valId, hits, win, need, kind) {
  const m = $(id);
  if (m.children.length !== win) {
    m.style.gridTemplateColumns = `repeat(${win}, 1fr)`;
    m.innerHTML = Array.from({ length: win }, () => '<div class="cell"></div>').join("");
  }
  [...m.children].forEach((c, i) => {
    c.className = "cell" + (i < hits ? ` on-${kind}` : "") + (i === need - 1 ? " target" : "");
  });
  $(valId).textContent = `${hits}/${win}`;
}

function renderAlerts(alerts) {
  const list = $("#alertList");
  $("#alertCount").textContent = `${alerts.length} this session`;
  if (!alerts.length) { list.innerHTML = '<div class="empty-small">No alerts. All clear.</div>'; return; }
  const sig = alerts.map((a) => `${a.incident_id || ""}-${a.time}-${a.type}`).join("|");
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = alerts.map((a) => `
    <div class="alert-item ${a.type}">
      ${a.snapshot ? `<img src="${a.snapshot}" alt="">` : "<div></div>"}
      <div>
        <div class="alert-top"><span class="chip chip-${a.type}">${a.type.toUpperCase()} · ${a.severity}</span><b class="mono">${pct(a.confidence)}</b></div>
        <div class="alert-meta">${esc(a.time)} · ${esc(a.camera)} · video t=${a.video_time}s</div>
      </div>
    </div>`).join("");
  // new alerts -> alarm overlay, toast, sound
  alerts.slice().reverse().forEach((a) => {
    const id = `${a.incident_id || ""}-${a.time}-${a.type}`;
    if (state.seenAlerts.has(id)) return;
    state.seenAlerts.add(id);
    toast(`🚨 <b>${a.type.toUpperCase()} DETECTED</b> on ${esc(a.camera)} (${pct(a.confidence)})`, a.type);
    bumpIncidentBadge();
    notifyBackground(a);
    // snooze: after the operator acknowledges, repeats of the same type only go to the feed for a while
    const snoozed = Date.now() - (state.ackAt[a.type] || 0) < prefs.snooze * 1000;
    const showing = !$("#alarm").hidden;
    if (!snoozed && (!showing || a.type === "fire")) {
      if (prefs.alarm) showAlarm(a);
      siren(a.type);
    }
  });
}

/* alert markers on the video timeline */
function renderMarks(s) {
  const box = $("#tlMarks");
  if (!s.duration || !s.alerts.length) { box.innerHTML = ""; return; }
  const sig = `${s.duration}|${s.alerts.length}`;
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = s.alerts.map((a) =>
    `<i class="tl-mark ${a.type}" style="left:${Math.min(100, (a.video_time / s.duration) * 100)}%" title="${a.type} at ${a.video_time}s"></i>`).join("");
}

function showAlarm(a) {
  const al = $("#alarm");
  state.alarmType = a.type;
  al.classList.toggle("smoke", a.type === "smoke");
  $("#alarmIcon").textContent = a.type === "fire" ? "🔥" : "💨";
  $("#alarmTitle").textContent = `${a.type.toUpperCase()} DETECTED`;
  $("#aCamera").textContent = a.camera;
  $("#aConf").textContent = pct(a.confidence);
  $("#aTime").textContent = a.time;
  $("#aSev").textContent = a.severity;
  const img = $("#aSnap");
  img.classList.toggle("has", !!a.snapshot);
  if (a.snapshot) img.src = a.snapshot;
  al.hidden = false;
}
state.ackAt = {};
$("#ackBtn").addEventListener("click", () => {
  $("#alarm").hidden = true;
  state.ackAt[state.alarmType] = Date.now();
});

/* ------------------------------------------------------------------ video tools: fullscreen + snapshot */
function toggleFullscreen() {
  const w = $("#videoWrap");
  if (document.fullscreenElement) document.exitFullscreen();
  else w.requestFullscreen?.().catch(() => toast("Fullscreen is not available", "err"));
}
function saveSnapshot() {
  const img = $("#stream");
  if (!state.running || !img.naturalWidth) { toast("Start monitoring first to take a snapshot", "err"); return; }
  const c = document.createElement("canvas");
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  c.getContext("2d").drawImage(img, 0, 0);
  c.toBlob((b) => {
    const a = document.createElement("a");
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    a.href = URL.createObjectURL(b);
    a.download = `visionguard_${(state.status?.camera || "camera").replace(/\W+/g, "_")}_${ts}.jpg`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast("📸 Snapshot saved");
  }, "image/jpeg", 0.92);
}
$("#fsBtn").addEventListener("click", toggleFullscreen);
$("#snapBtn").addEventListener("click", saveSnapshot);

/* ------------------------------------------------------------------ keyboard shortcuts */
$("#helpBtn").addEventListener("click", () => { $("#help").hidden = false; });
$$("[data-close]").forEach((b) => b.addEventListener("click", () => { $(`#${b.dataset.close}`).hidden = true; }));
$("#help").addEventListener("click", (e) => { if (e.target.id === "help") $("#help").hidden = true; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("#alarm").hidden) $("#ackBtn").click();
    closeModal(); $("#help").hidden = true; $("#prefs").hidden = true;
    if ($("#emailModal")) $("#emailModal").hidden = true;
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest("input, select, textarea")) return;
  const k = e.key.toLowerCase();
  if (k === " ") { e.preventDefault(); (state.running ? $("#stopBtn") : $("#startBtn")).click(); }
  else if (k === "f") toggleFullscreen();
  else if (k === "s") saveSnapshot();
  else if (k === "m") setSound(!prefs.sound);
  else if (k === "t") toggleTheme();
  else if (k === "1") location.hash = "#live";
  else if (k === "2") location.hash = "#incidents";
  else if (k === "3") location.hash = "#analytics";
  else if (e.key === "?") $("#help").hidden = false;
});

/* ------------------------------------------------------------------ confidence chart (canvas) */
let chartSpan = 30;
function resetChart() { drawChart([], +$("#conf").value); }
function drawChart(trace, thr) {
  const c = $("#chart"), dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const pad = { l: 30, r: 8, t: 8, b: 18 }, W = w - pad.l - pad.r, H = h - pad.t - pad.b;
  const css = getComputedStyle(document.documentElement);
  const cv = (n) => css.getPropertyValue(n).trim();
  const fire = cv("--fire"), smoke = cv("--smoke");
  g.font = "10px Consolas, monospace"; g.fillStyle = cv("--dim"); g.strokeStyle = cv("--line"); g.lineWidth = 1;
  [0, 0.5, 1].forEach((v) => {
    const y = pad.t + H - v * H;
    g.beginPath(); g.moveTo(pad.l, y); g.lineTo(pad.l + W, y); g.stroke();
    g.fillText(`${v * 100}%`, 0, y + 3);
  });
  const tMax = trace.length ? trace[trace.length - 1][0] : chartSpan;
  const tMin = Math.max(0, tMax - chartSpan);
  const X = (t) => pad.l + ((t - tMin) / Math.max(1e-6, tMax - tMin || chartSpan)) * W;
  const Y = (v) => pad.t + H - v * H;
  g.fillText(`${tMin.toFixed(0)}s`, pad.l, h - 4);
  g.fillText(`${tMax.toFixed(0)}s`, pad.l + W - 22, h - 4);
  // threshold
  g.setLineDash([4, 4]); g.strokeStyle = cv("--muted");
  g.beginPath(); g.moveTo(pad.l, Y(thr)); g.lineTo(pad.l + W, Y(thr)); g.stroke(); g.setLineDash([]);
  const pts = trace.filter((p) => p[0] >= tMin);
  const series = (idx, color, fill) => {
    if (pts.length < 2) return;
    g.beginPath();
    pts.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[idx])) : g.moveTo(X(p[0]), Y(p[idx]))));
    g.strokeStyle = color; g.lineWidth = 2; g.stroke();
    g.lineTo(X(pts[pts.length - 1][0]), Y(0)); g.lineTo(X(pts[0][0]), Y(0)); g.closePath();
    const grad = g.createLinearGradient(0, pad.t, 0, pad.t + H);
    grad.addColorStop(0, fill); grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad; g.fill();
  };
  series(2, smoke, "rgba(245,165,36,.22)");
  series(1, fire, "rgba(255,77,61,.25)");
}
window.addEventListener("resize", () => state.status && drawChart(state.status.trace, +$("#conf").value));

/* ------------------------------------------------------------------ incidents */
function bumpIncidentBadge() {
  const b = $("#incidentCount");
  b.textContent = (+b.textContent || 0) + 1;
  b.classList.add("hot");
  const view = (location.hash || "#live").slice(1);
  if (view === "analytics") setTimeout(loadStats, 500);
  if (view === "incidents") setTimeout(loadIncidents, 800);
}

async function loadIncidents() {
  try { state.incidents = await api("/api/incidents"); } catch (e) { toast(esc(e.message), "err"); return; }
  $("#incidentCount").textContent = state.incidents.length;
  $("#incidentCount").classList.toggle("hot", state.incidents.some((i) => i.status === "ACTIVE"));
  renderIncidents();
}

function renderIncidents() {
  const q = $("#incSearch").value.toLowerCase();
  const rows = state.incidents.filter((r) =>
    (state.incFilter === "all" || r.type === state.incFilter) &&
    (state.incReview === "all" ||
      (state.incReview === "CONFIRMED" ? (r.review === "CONFIRMED" || r.review === "CONFIRMED_FIRE") :
       (r.review || "PENDING") === state.incReview)) &&
    (!q || `${r.camera} ${r.source} ${r.note || ""}`.toLowerCase().includes(q)));
  const all = state.incidents;
  const cnt = (f) => all.filter(f).length;
  $("#incStats").innerHTML = `
    <div class="inc-stat"><b>${all.length}</b>incidents</div>
    <div class="inc-stat"><b style="color:var(--fire)">${cnt((r) => r.type === "fire")}</b>fire</div>
    <div class="inc-stat"><b style="color:var(--smoke)">${cnt((r) => r.type === "smoke")}</b>smoke</div>
    <div class="inc-stat"><b>${cnt((r) => r.status === "ACTIVE")}</b>active</div>
    <div class="inc-stat"><b>${cnt((r) => (r.review || "PENDING") === "PENDING")}</b>not reviewed</div>
    <div class="inc-stat"><b style="color:var(--fire)">${cnt((r) => r.review === "CONFIRMED" || r.review === "CONFIRMED_FIRE")}</b>fire</div>
    <div class="inc-stat"><b style="color:var(--smoke)">${cnt((r) => r.review === "CONFIRMED_SMOKE")}</b>smoke</div>
    <div class="inc-stat"><b style="color:var(--ok)">${cnt((r) => r.review === "FALSE_ALARM")}</b>false alarms</div>`;
  if (!rows.length) {
    $("#incGrid").innerHTML = `<div class="empty"><b>No incidents${all.length ? " match the filter" : " yet"}</b>
      Confirmed fire and smoke events appear here with their detection frame and video clip.</div>`;
    return;
  }
  $("#incGrid").innerHTML = rows.map((r) => `
    <div class="inc-card" data-id="${r.id}">
      <div class="inc-thumb">
        ${r.snapshot_url ? `<img src="${r.snapshot_url}" alt="" loading="lazy">` : ""}
        <div class="tags"><span class="chip chip-${r.type}">${r.type.toUpperCase()}</span>
          <span class="chip ${r.status === "ACTIVE" ? "chip-fire" : "chip-dim"}">${r.status}</span></div>
        ${r.clip_url ? '<div class="play"><svg viewBox="0 0 24 24"><path d="M7 5l12 7-12 7z"/></svg></div>' : ""}
        ${(r.review === "CONFIRMED" || r.review === "CONFIRMED_FIRE") ? '<span class="chip chip-review-confirmed review-chip">🔥 CONFIRMED FIRE</span>' : ""}
        ${r.review === "CONFIRMED_SMOKE" ? '<span class="chip chip-review-smoke review-chip">💨 CONFIRMED SMOKE</span>' : ""}
        ${r.review === "FALSE_ALARM" ? '<span class="chip chip-review-false review-chip">✕ FALSE ALARM</span>' : ""}
      </div>
      <div class="inc-body">
        <div class="inc-title"><span>#${r.id} · ${esc(r.camera)}</span><span class="mono">${pct(r.confidence)}</span></div>
        <div class="inc-meta">${fmtDate(r.started_at)} · ${r.severity} · ${esc(r.source)}</div>
        ${r.note ? `<div class="inc-meta" title="${esc(r.note)}">📝 ${esc(r.note)}</div>` : ""}
      </div>
    </div>`).join("");
  $$(".inc-card").forEach((c) => c.addEventListener("click", () => openIncident(+c.dataset.id)));
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });
}

function openIncident(id) {
  const r = state.incidents.find((x) => x.id === id);
  if (!r) return;
  const v = $("#mVideo");
  if (r.clip_url) { v.src = r.clip_url; v.style.display = "block"; } else { v.removeAttribute("src"); v.style.display = "none"; }
  $("#mImg").src = r.snapshot_url || "";
  $("#mInfo").innerHTML = `
    <span class="chip chip-${r.type}">${r.type.toUpperCase()} · ${r.severity}</span>
    <h3 style="margin-top:10px">Incident #${r.id}</h3>
    <div class="muted">${fmtDate(r.started_at)}</div>
    <div class="kv">
      <span>Camera</span><b>${esc(r.camera)}</b>
      <span>Type</span><b>${r.type}</b>
      <span>Severity</span><b>${r.severity}</b>
      <span>Confidence</span><b>${pct(r.confidence)} (max)</b>
      <span>Status</span><b>${r.status}</b>
      <span>Started</span><b>${fmtDate(r.started_at)}</b>
      <span>Ended</span><b>${r.ended_at ? fmtDate(r.ended_at) : "–"}</b>
      <span>Video time</span><b>${r.video_time_sec} s</b>
      <span>Source</span><b>${esc(r.source)}</b>
    </div>
    <div class="review-box">
      <h3>Operator review</h3>
      <div class="muted small">Was this a real fire/smoke event? Reviews feed the false-alarm statistics.</div>
      <div class="review-btns">
        <button class="btn btn-review ${(r.review === "CONFIRMED" || r.review === "CONFIRMED_FIRE") ? "on-confirm" : ""}" data-review="CONFIRMED">🔥 Confirmed fire</button>
        <button class="btn btn-review ${r.review === "CONFIRMED_SMOKE" ? "on-smoke" : ""}" data-review="CONFIRMED_SMOKE">💨 Confirmed smoke</button>
        <button class="btn btn-review ${r.review === "FALSE_ALARM" ? "on-false" : ""}" data-review="FALSE_ALARM">✕ False alarm</button>
      </div>
      <textarea id="mNote" placeholder="Add a note (e.g. 'fire brigade called', 'sunlight reflection')…">${esc(r.note || "")}</textarea>
      <div class="modal-actions">
        <button class="btn btn-primary btn-sm" id="mSave">Save note</button>
        ${r.review && r.review !== "PENDING" ? '<button class="btn btn-ghost btn-sm" data-review="PENDING">Reset review</button>' : ""}
        ${r.clip_url ? `<a class="btn btn-ghost btn-sm" href="${r.clip_url}" download>Download clip</a>` : ""}
        <button class="btn btn-danger btn-sm" id="mDelete" style="margin-left:auto">Delete</button>
      </div>
    </div>
    <p class="muted small" style="margin-top:16px">The clip covers 3 s before to 5 s after the confirmation. Below the clip is the detection frame.</p>`;
  $$("#mInfo [data-review]").forEach((b) => b.addEventListener("click", () => saveReview(r.id, { review: b.dataset.review })));
  $("#mSave").addEventListener("click", () => saveReview(r.id, { note: $("#mNote").value }, "Note saved"));
  $("#mDelete").addEventListener("click", async () => {
    if (!confirm(`Delete incident #${r.id} and its snapshot and clip?`)) return;
    await api(`/api/incidents/${r.id}`, { method: "DELETE" });
    closeModal();
    toast(`Incident #${r.id} deleted`);
    loadIncidents();
  });
  $("#modal").hidden = false;
}

async function saveReview(id, body, msg) {
  try {
    const updated = await api(`/api/incidents/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const i = state.incidents.findIndex((x) => x.id === id);
    if (i >= 0) state.incidents[i] = { ...state.incidents[i], ...updated };
    renderIncidents();
    openIncident(id);
    toast(msg || ({
      CONFIRMED: "Marked as <b>confirmed fire</b>",
      CONFIRMED_FIRE: "Marked as <b>confirmed fire</b>",
      CONFIRMED_SMOKE: "Marked as <b>confirmed smoke</b>",
      FALSE_ALARM: "Marked as <b>false alarm</b>",
      PENDING: "Review reset"
    }[body.review]));
  } catch (e) { toast(`Could not save: ${esc(e.message)}`, "err"); }
}
function closeModal() { $("#modal").hidden = true; $("#mVideo").pause(); }
$("#modalClose").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

$$("#incFilter .seg-btn").forEach((b) => b.addEventListener("click", () => {
  state.incFilter = b.dataset.f;
  $$("#incFilter .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  renderIncidents();
}));
$("#incSearch").addEventListener("input", renderIncidents);
$("#incReview").addEventListener("change", (e) => { state.incReview = e.target.value; renderIncidents(); });
$("#incRefresh").addEventListener("click", loadIncidents);
$("#incClear").addEventListener("click", async () => {
  if (!confirm("Delete all incidents from the history?")) return;
  await api("/api/incidents", { method: "DELETE" });
  toast("Incident history cleared");
  loadIncidents();
});

/* ------------------------------------------------------------------ live statistics (from the incident DB) */
function donut(el, parts, centerTop, centerSub) {
  const total = parts.reduce((s, p) => s + p.v, 0);
  let acc = 0;
  const stops = total ? parts.map((p) => {
    const a = (acc / total) * 360; acc += p.v; const b = (acc / total) * 360;
    return `${p.c} ${a}deg ${b}deg`;
  }).join(", ") : "var(--panel-2) 0deg 360deg";
  el.innerHTML = `
    <div class="donut" style="background: conic-gradient(${stops})">
      <div class="donut-center">${centerTop}<small>${centerSub}</small></div></div>
    <div class="legend-list">${parts.map((p) => `<div><i style="background:${p.c}"></i>${p.l}: <b>${p.v}</b></div>`).join("")}</div>`;
}

async function loadStats() {
  let d;
  try { d = await api("/api/stats"); } catch (_) { return; }
  const max = Math.max(1, ...d.by_day.map((x) => x.fire + x.smoke));
  $("#statDays").innerHTML = d.by_day.map((x) => {
    const tot = x.fire + x.smoke, day = new Date(`${x.day}T00:00`);
    return `<div class="bar-col" title="${x.day}: ${x.fire} fire, ${x.smoke} smoke">
      <div class="bar-val">${tot || ""}</div>
      <div class="bar-stack" style="height:${(tot / max) * 100}%">
        <div class="seg-fire" style="flex:${x.fire}"></div><div class="seg-smoke" style="flex:${x.smoke}"></div></div>
      <div class="bar-lbl">${day.toLocaleDateString([], { day: "2-digit", month: "short" }).replace(" ", " ")}</div></div>`;
  }).join("");
  donut($("#statType"), [
    { l: "Fire", v: d.by_type.fire || 0, c: "var(--fire)" },
    { l: "Smoke", v: d.by_type.smoke || 0, c: "var(--smoke)" },
  ], d.total, "incidents");
  const r = d.by_review;
  donut($("#statReview"), [
    { l: "Confirmed Fire", v: (r.CONFIRMED || 0) + (r.CONFIRMED_FIRE || 0), c: "var(--fire)" },
    { l: "Confirmed Smoke", v: r.CONFIRMED_SMOKE || 0, c: "var(--smoke)" },
    { l: "False alarm", v: r.FALSE_ALARM || 0, c: "var(--ok)" },
    { l: "Not reviewed", v: r.PENDING || 0, c: "var(--line-2)" },
  ], d.false_alarm_rate == null ? "–" : `${Math.round(d.false_alarm_rate * 100)}%`, "false alarms");
}

/* ------------------------------------------------------------------ analytics */
async function loadAnalytics() {
  loadStats();
  let d;
  try { d = await api("/api/analytics"); } catch (e) { toast(esc(e.message), "err"); return; }
  const m = Object.fromEntries(d.system.map((r) => [r.metric, r.value]));
  if (d.settings) $("#anSettings").textContent = `Full pipeline on unseen test videos · ${d.settings}`;
  const get = (k) => m[k] || "–";
  const delay = get("alert delay, mean / median (s)").split("/");
  const prf = get("video-level precision / recall / F1").split("/");
  const tiles = [
    ["Fire events detected", get("fire/smoke events detected"), "test videos with fire/smoke", "val-ok"],
    ["Missed events", get("missed fire events"), "fire videos without an alert", get("missed fire events") === "0" ? "val-ok" : "val-smoke"],
    ["False alarms", get("false alarms (total)"), `${get("false alarms per hour (all video)")} per hour`, get("false alarms (total)") === "0" ? "val-ok" : "val-smoke"],
    ["Alert delay", `${(delay[0] || "–").trim()} s`, `median ${(delay[1] || "–").trim()} s`, ""],
    ["Precision / Recall", `${(prf[0] || "").trim()} / ${(prf[1] || "").trim()}`, `F1 ${(prf[2] || "–").trim()}`, ""],
    ["Processing speed", get("YOLO processing speed").replace(" (CPU)", ""), `${get("end-to-end speed")} on CPU`, ""],
  ];
  $("#anKpis").innerHTML = d.system.length ? tiles.map(([l, v, s, c]) =>
    `<div class="an-kpi"><div class="l">${l}</div><div class="v ${c}">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join("")
    : `<div class="empty"><b>No evaluation yet</b>Run <code>python -m system_test.evaluate_system</code>.</div>`;

  $("#anVideoCount").textContent = d.videos.length ? `${d.videos.length} videos` : "";
  $("#anVideos").innerHTML = d.videos.length ? `
    <tr><th>Video</th><th>Truth</th><th>Result</th><th class="num">First alert</th><th class="num">Alerts</th><th class="num">FPS</th></tr>
    ${d.videos.map((v) => {
      const pos = v.label === "positive";
      const res = pos ? (v.detected === "True" ? ["Detected", "chip-ok"] : ["Missed", "chip-fire"])
                      : (+v.false_alarms ? ["False alarm", "chip-smoke"] : ["Correct (no alert)", "chip-ok"]);
      return `<tr><td>${esc(v.video.replace("__", " · "))}</td>
        <td>${pos ? '<span class="chip chip-fire">FIRE/SMOKE</span>' : '<span class="chip chip-dim">NO FIRE</span>'}</td>
        <td><span class="chip ${res[1]}">${res[0]}</span></td>
        <td class="num">${v.first_alert_s ? `${v.first_alert_s} s` : "–"}</td><td class="num">${v.alerts}</td><td class="num">${v.yolo_fps}</td></tr>`;
    }).join("")}` : "";

  $("#anSelected").textContent = d.selected ? `selected: ${d.selected}` : "";
  $("#anModel").innerHTML = d.model_images.length ? `
    <tr><th>Model</th><th>Class</th><th class="num">P</th><th class="num">R</th><th class="num">F1</th><th class="num">mAP50</th><th class="num">mAP50-95</th></tr>
    ${d.model_images.map((r) => `<tr class="${r.model === d.selected ? "sel" : ""}"><td>${esc(r.model)}</td><td>${esc(r.class)}</td>
      <td class="num">${r.Precision}</td><td class="num">${r.Recall}</td><td class="num">${r.F1}</td>
      <td class="num">${r.mAP50}</td><td class="num">${r["mAP50-95"]}</td></tr>`).join("")}` : "";

  $("#anSweep").innerHTML = d.sweep.length ? `
    <tr><th class="num">Conf</th><th>Confirm</th><th class="num">Detected</th><th class="num">False alarms</th><th class="num">Delay s</th></tr>
    ${d.sweep.map((r) => {
      const sel = d.settings && d.settings.includes(`conf >= ${r.conf},`) && d.settings.includes(`confirm ${r.confirm}`);
      return `<tr class="${sel ? "sel" : ""}"><td class="num">${r.conf}</td><td>${r.confirm}${sel ? " ✓" : ""}</td>
        <td class="num">${r.detected}</td><td class="num">${r["false alarms"]}</td><td class="num">${r["mean delay s"]}</td></tr>`;
    }).join("")}` : "";

  $("#anCm").innerHTML = d.confusion.map((u) =>
    `<figure><img src="${u}" alt="" loading="lazy"><figcaption>${esc(u.split("/").pop().replace(".png", "").replace("confusion_matrix_", "model: "))}</figcaption></figure>`).join("")
    || '<p class="muted">Run <code>python -m training.evaluate</code> to create confusion matrices.</p>';
}

/* ------------------------------------------------------------------ email settings */
async function loadEmailConfig() {
  try {
    const cfg = await api("/api/email");
    if ($("#emailAlerts")) $("#emailAlerts").checked = !!cfg.enabled;
    if ($("#mEmailEnabled")) $("#mEmailEnabled").checked = !!cfg.enabled;
    if ($("#mEmailTo")) $("#mEmailTo").value = cfg.to || "";
    if ($("#mEmailHost")) $("#mEmailHost").value = cfg.host || "";
    if ($("#mEmailPort")) $("#mEmailPort").value = cfg.port || 587;
    if ($("#mEmailUser")) $("#mEmailUser").value = cfg.user || "";
    if ($("#mEmailPassword") && cfg.has_password) {
      $("#mEmailPassword").placeholder = "••••••••  (Saved)";
    }
  } catch (e) {
    console.warn("Failed to load email config", e);
  }
}

function openEmailModal() {
  loadEmailConfig();
  const fb = $("#emailTestFeedback");
  if (fb) { fb.style.display = "none"; fb.textContent = ""; }
  const m = $("#emailModal");
  if (m) m.hidden = false;
}
window.openEmailModal = openEmailModal;

$("#emailBtn")?.addEventListener("click", openEmailModal);
$("#cfgEmailBtn")?.addEventListener("click", openEmailModal);
$("#emailModal")?.addEventListener("click", (e) => {
  if (e.target.id === "emailModal") $("#emailModal").hidden = true;
});

$("#togglePwdBtn")?.addEventListener("click", () => {
  const inp = $("#mEmailPassword");
  if (!inp) return;
  if (inp.type === "password") {
    inp.type = "text";
    $("#togglePwdBtn").textContent = "🙈";
  } else {
    inp.type = "password";
    $("#togglePwdBtn").textContent = "👁";
  }
});

$("#emailAlerts")?.addEventListener("change", async (e) => {
  const enabled = e.target.checked;
  try {
    await api("/api/email", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if ($("#mEmailEnabled")) $("#mEmailEnabled").checked = enabled;
    toast(enabled ? "✉️ Email alerts enabled" : "Email alerts disabled");
  } catch (err) {
    toast(`Failed to update email setting: ${esc(err.message)}`, "err");
  }
});

$("#presetGmail")?.addEventListener("click", () => {
  $("#mEmailHost").value = "smtp.gmail.com";
  $("#mEmailPort").value = 587;
  $("#mEmailPwdHint").innerHTML = "For Gmail: Use an <b>App Password</b> (Google Account &rarr; Security &rarr; 2-Step Verification &rarr; App passwords).";
});
$("#presetOutlook")?.addEventListener("click", () => {
  $("#mEmailHost").value = "smtp-mail.outlook.com";
  $("#mEmailPort").value = 587;
  $("#mEmailPwdHint").textContent = "For Outlook / Office 365: Use your email and password / app password.";
});
$("#presetCustom")?.addEventListener("click", () => {
  $("#mEmailHost").value = "";
  $("#mEmailPort").value = 587;
  $("#mEmailPwdHint").textContent = "Enter your custom SMTP server details and credentials.";
});

$("#mEmailSaveBtn")?.addEventListener("click", async () => {
  const data = {
    enabled: $("#mEmailEnabled").checked,
    to: $("#mEmailTo").value.trim(),
    host: $("#mEmailHost").value.trim(),
    port: +$("#mEmailPort").value || 587,
    user: $("#mEmailUser").value.trim(),
  };
  const pwd = $("#mEmailPassword").value;
  if (pwd) data.password = pwd;

  if (data.enabled && !data.to) {
    toast("Please enter a recipient email address", "err");
    return;
  }

  $("#mEmailSaveBtn").disabled = true;
  try {
    await api("/api/email", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if ($("#emailAlerts")) $("#emailAlerts").checked = data.enabled;
    toast("✓ Email settings saved successfully");
    $("#emailModal").hidden = true;
  } catch (err) {
    toast(`Could not save email settings: ${esc(err.message)}`, "err");
  } finally {
    $("#mEmailSaveBtn").disabled = false;
  }
});

$("#mEmailTestBtn")?.addEventListener("click", async () => {
  const data = {
    to: $("#mEmailTo").value.trim(),
    host: $("#mEmailHost").value.trim(),
    port: +$("#mEmailPort").value || 587,
    user: $("#mEmailUser").value.trim(),
  };
  const pwd = $("#mEmailPassword").value;
  if (pwd) data.password = pwd;

  if (!data.to) {
    toast("Please enter a recipient email address for testing", "err");
    return;
  }
  if (!data.host) {
    toast("Please enter the SMTP Host", "err");
    return;
  }

  const fb = $("#emailTestFeedback");
  fb.style.display = "block";
  fb.style.background = "var(--panel-2)";
  fb.style.border = "1px solid var(--line-2)";
  fb.style.color = "var(--text)";
  fb.textContent = "⏳ Sending test email, please wait...";
  $("#mEmailTestBtn").disabled = true;

  try {
    const res = await api("/api/email/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    fb.style.background = "var(--ok-soft)";
    fb.style.border = "1px solid var(--ok)";
    fb.style.color = "var(--ok)";
    fb.textContent = "✓ " + (res.message || "Test email sent successfully! Please check your inbox.");
    toast("✓ Test email sent!", "ok");
  } catch (err) {
    fb.style.background = "var(--fire-soft)";
    fb.style.border = "1px solid var(--fire)";
    fb.style.color = "#ff8a80";
    fb.textContent = "✕ " + err.message;
    toast("Failed to send test email", "err");
  } finally {
    $("#mEmailTestBtn").disabled = false;
  }
});

/* ------------------------------------------------------------------ boot */
(async function init() {
  route();
  try { await loadSources(); } catch (e) { toast(`Could not load sources: ${esc(e.message)}`, "err"); }
  loadIncidents();
  loadEmailConfig();
  connectEvents();
  resetChart();
  renderMeter("#meterFire", "#meterFireVal", 0, +$("#window").value || 10, +$("#minHits").value || 5, "fire");
  renderMeter("#meterSmoke", "#meterSmokeVal", 0, +$("#window").value || 10, +$("#minHits").value || 5, "smoke");
})();

