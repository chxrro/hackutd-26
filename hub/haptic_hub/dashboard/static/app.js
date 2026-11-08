"use strict";
// Haptic Hub dashboard. Talks to the hub over /ws: JSON "state" messages and
// binary JPEG previews in, the same "control" requests as the phone out.

const $ = (id) => document.getElementById(id);
const MOTOR_NAMES = ["Far left", "Left", "Center", "Right", "Far right"];
const STALE_FRAME_MS = 1000;

let ws = null;
let nextId = 1;
let pending = new Map(); // request id -> description
let lastAnnounced = "";
let previewUrl = null;
let lastState = null;

// ---------------------------------------------------------------- theme

const THEMES = ["auto", "light", "dark"];
function applyTheme(theme) {
  if (theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  $("theme").textContent = `Theme: ${theme}`;
  $("theme").setAttribute("aria-label", `Color theme: ${theme === "auto" ? "automatic" : theme}. Activate to change.`);
}
function savedTheme() {
  try { return THEMES.includes(localStorage.getItem("theme")) ? localStorage.getItem("theme") : "auto"; } catch { return "auto"; }
}
$("theme").addEventListener("click", () => {
  const next = THEMES[(THEMES.indexOf(savedTheme()) + 1) % THEMES.length];
  try { localStorage.setItem("theme", next); } catch { /* private mode: not remembered */ }
  applyTheme(next);
});
applyTheme(savedTheme());

// ---------------------------------------------------------------- connection

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "blob";
  ws.addEventListener("message", (ev) => {
    if (typeof ev.data === "string") onText(ev.data);
    else showPreview(ev.data);
  });
  ws.addEventListener("close", () => {
    ws = null;
    pending.clear();
    setBanner({ level: "fault", title: "FAULT: dashboard lost the hub", detail: "No data from the hub. Reconnecting… Do not rely on the belt." });
    setControlsEnabled(false);
    setTimeout(connect, 1000);
  });
}

function onText(text) {
  let msg;
  try { msg = JSON.parse(text); } catch { return; }
  if (msg.type === "state") render(msg);
  else if (msg.type === "ack") onAck(msg);
}

function send(action, settings) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const id = nextId++;
  const msg = { type: "control", id, action };
  if (settings) msg.settings = settings;
  pending.set(id, settings ? Object.entries(settings).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(", ") : action.replace("_", " "));
  ws.send(JSON.stringify(msg));
  setAck(`Sending: ${pending.get(id)}…`, true);
}

function onAck(ack) {
  const what = pending.get(ack.id) || "request";
  pending.delete(ack.id);
  setAck(ack.ok ? `Done: ${what}.` : `Refused (${what}): ${ack.error}.`, ack.ok);
  if (lastState) renderControls(lastState, ack.settings);
}

function setAck(text, ok) {
  $("ack").textContent = text;
  $("ack").dataset.ok = String(ok);
}

// ---------------------------------------------------------------- preview

function showPreview(blob) {
  const url = URL.createObjectURL(blob);
  const img = $("preview");
  img.onload = () => { if (previewUrl && previewUrl !== url) URL.revokeObjectURL(previewUrl); previewUrl = url; };
  img.src = url;
}

// ---------------------------------------------------------------- render

function fmtMs(v) { return v == null ? "–" : `${Math.round(v)} ms`; }

function setBanner(b) {
  const el = $("banner");
  el.dataset.level = b.level;
  $("banner-title").textContent = b.title;
  $("banner-detail").textContent = b.detail;
  $("banner-icon").textContent = { ok: "✓", paused: "II", test: "~", fault: "!" }[b.level] || "!";
  const said = `${b.title}. ${b.detail}`;
  if (said !== lastAnnounced) { // announce changes only, not every update
    lastAnnounced = said;
    $("announce").textContent = said;
  }
}

function render(state) {
  lastState = state;
  const s = state.status;
  setBanner(state.banner);

  const src = $("source");
  // phone frames that aren't confirmed glasses (mock, test pattern, unknown) lose the glasses styling
  src.dataset.kind = state.source.verified === false ? "unverified" : state.source.kind;
  $("source-label").textContent = state.source.label;
  const d = state.source.details || {};
  // a file path can be very long: show its last part, keep the full name on hover
  const parts = [state.source.name.replace(/^([a-z]+:).*[\\/]/, "$1")];
  if (d.phones !== undefined) parts.push(`${d.phones} phone${d.phones === 1 ? "" : "s"}`, `stream: ${d.phone_stream || "unknown"}`);
  if (state.source.frame_source !== undefined) parts.push(`frames: ${state.source.frame_source}`);
  if (d.frames !== undefined) parts.push(`${d.frames} frames`);
  $("source-details").textContent = parts.join(" · ");
  $("source-details").title = state.source.name;

  $("inference").textContent = fmtMs(s.inference_ms);
  $("frame-age").textContent = fmtMs(s.frame_age_ms);
  $("perception").textContent = state.perception;

  // live view note: say plainly when the picture isn't current
  const note = $("frame-note");
  const stale = s.frame_age_ms == null || s.frame_age_ms > STALE_FRAME_MS;
  if (stale) note.textContent = s.frame_age_ms == null ? "No camera frames yet" : `Frozen: no new frame for ${(s.frame_age_ms / 1000).toFixed(1)} s`;
  else if (!state.preview.live_boxes) note.textContent = "Raw frame: detections not current";
  note.hidden = !stale && state.preview.live_boxes;
  $("preview").alt = stale ? "Last camera frame, not current." : state.preview.live_boxes
    ? "Live camera frame. Detected people are boxed in magenta, objects in orange."
    : "Live camera frame without detections.";

  renderBelt(state);
  renderControls(state, s.settings);
  renderCloud(state.classifier);

  $("code").textContent = state.pairing.code;
  $("phone-address").textContent = state.pairing.phone_address || (state.source.glasses ? "–" : "not listening (source is not the glasses)");
  $("paired").textContent = String(state.pairing.paired_phones);
}

function renderBelt(state) {
  const s = state.status;
  const mode = $("mode");
  mode.dataset.mode = s.mode;
  mode.textContent = s.mode.toUpperCase();
  $("belt-state").textContent = state.belt.simulated
    ? "Simulated belt: nothing physical vibrates."
    : `Bluetooth belt: ${state.belt.state}.`;

  const list = $("motors");
  list.dataset.mode = s.mode;
  if (list.children.length !== s.levels.length) {
    list.replaceChildren(...s.levels.map((_, i) => {
      const li = document.createElement("li");
      li.className = "motor";
      li.innerHTML = '<div class="track" role="meter" aria-valuemin="0" aria-valuemax="255"><div class="fill"></div></div>'
        + '<span class="motor-name"></span><span class="motor-level"></span><span class="motor-kind"></span>';
      li.querySelector(".motor-name").textContent = MOTOR_NAMES[i] || `Motor ${i + 1}`;
      return li;
    }));
  }
  const sectors = s.sectors || [];
  [...list.children].forEach((li, i) => {
    const level = s.levels[i];
    const name = MOTOR_NAMES[i] || `Motor ${i + 1}`;
    const track = li.querySelector(".track");
    track.setAttribute("aria-valuenow", String(level));
    track.setAttribute("aria-label", `${name} motor`);
    track.setAttribute("aria-valuetext", s.mode === "fault" ? "fault pattern" : level ? `${Math.round(level / 2.55)} percent` : "off");
    li.querySelector(".fill").style.height = `${(s.mode === "levels" ? level / 255 : 0) * 100}%`;
    li.querySelector(".motor-level").textContent = s.mode === "fault" ? "fault" : level ? String(level) : "off";
    const kind = sectors[i] ? sectors[i].kind : null;
    const k = li.querySelector(".motor-kind");
    k.dataset.kind = kind || "";
    k.textContent = kind || "";
  });
}

function renderControls(state, settings) {
  const s = state.status;
  const paused = settings.paused;
  const testing = s.reason === "belt test";
  const pause = $("pause");
  pause.setAttribute("aria-pressed", String(paused));
  pause.textContent = paused ? "Resume alerts" : "Pause alerts";
  $("belt-test").textContent = `Belt test (${state.options.belt_test_s} s sweep)`;
  $("belt-test").disabled = paused || testing;
  $("belt-test").title = paused ? "Resume alerts first" : "";

  const opts = state.options;
  for (const input of document.querySelectorAll('input[name="classifier"]')) {
    const allowed = opts.classifier && (input.value === "local" || opts.nim);
    input.disabled = !allowed;
    input.parentElement.classList.toggle("disabled", !allowed);
    if (document.activeElement !== input || pending.size === 0) input.checked = input.value === settings.classifier;
  }
  $("classifier-hint").textContent = !opts.classifier
    ? "Not available: this perception has no detector."
    : opts.nim ? "" : "Cloud needs a cloud endpoint when the hub starts (--classifier nim, --nim-url or --nim-env).";

  const ranges = $("ranges");
  const names = Object.keys(opts.alert_ranges);
  if (ranges.children.length !== names.length) {
    ranges.replaceChildren(...names.map((name) => {
      const r = opts.alert_ranges[name];
      const label = document.createElement("label");
      label.innerHTML = '<input type="radio" name="alert_range"> <span></span>';
      label.querySelector("input").value = name;
      label.querySelector("span").textContent = `${name[0].toUpperCase()}${name.slice(1)}: from ${r.far_m} m / ${r.ttc_far_s} s`;
      label.querySelector("input").addEventListener("change", () => send("set", { alert_range: name }));
      return label;
    }));
  }
  for (const input of ranges.querySelectorAll("input")) {
    input.disabled = false;
    input.checked = input.value === settings.alert_range;
  }
  setControlsEnabled(true);
}

function renderCloud(c) {
  const on = c && c.backend === "nim";
  $("cloud-state").textContent = !c ? "not available"
    : on ? (c.stale ? "stale: local labels in use" : c.error ? "error" : "on") : "off (local labels)";
  $("cloud-model").textContent = on ? c.model : "–";
  $("cloud-rtt").textContent = on ? fmtMs(c.rtt_ms) : "–";
  $("cloud-last").textContent = on ? (c.last_ok_age_s == null ? "no reply yet" : `${c.last_ok_age_s.toFixed(1)} s ago`) : "–";
  $("cloud-error").textContent = on ? (c.error || "none") : "–";
}

function setControlsEnabled(on) {
  for (const el of document.querySelectorAll(".controls button, .controls input")) {
    if (!on) el.disabled = true;
  }
  if (on) $("pause").disabled = false;
}

// ---------------------------------------------------------------- inputs

$("pause").addEventListener("click", () => send($("pause").getAttribute("aria-pressed") === "true" ? "resume" : "pause"));
$("belt-test").addEventListener("click", () => send("belt_test"));
for (const input of document.querySelectorAll('input[name="classifier"]')) {
  input.addEventListener("change", () => send("set", { classifier: input.value }));
}
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("code").textContent);
    setAck("Control code copied.", true);
  } catch {
    setAck("Copy failed: select the code and copy it by hand.", false);
  }
});

setControlsEnabled(false);
connect();
