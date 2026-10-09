/* Hush UI. Talks to Python through window.pywebview.api; Python pushes events via window.flowEvent(name, data). */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n || 0).toLocaleString();
const S = { settings: null, stats: null, stt: null, llm: null, platform: "win", hist: [], histQ: "", dict: [], snips: [], tfs: [], notes: [], noteSel: null, styleCat: "personal", recording: false, level: 0 };
let api = null;

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M7 5l12 7-12 7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg>',
  flag: '<svg viewBox="0 0 24 24"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M5 7l1 13h12l1-13M9 7V4h6v3"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="M12 3l2.7 5.6 6.1.8-4.4 4.3 1 6.1L12 17l-5.4 2.8 1-6.1-4.4-4.3 6.1-.8z"/></svg>',
  redo: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>',
  ai: '<svg class="i" viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 7V4M9 13h.01M15 13h.01"/></svg>',
  other: '<svg class="i" viewBox="0 0 24 24"><path d="M7 9a3 3 0 1 0 0 6c3 0 7-6 10-6a3 3 0 1 1 0 6c-3 0-7-6-10-6z"/></svg>',
  documents: '<svg class="i" viewBox="0 0 24 24"><path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M9 13h6M9 17h6"/></svg>',
  email: '<svg class="i" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
  personal: '<svg class="i" viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
  work: '<svg class="i" viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4z"/><path d="M8 9h8M8 12h5"/></svg>',
};
const CAT_LABEL = { ai: "AI prompts", other: "Other tasks", documents: "Documents", email: "Emails", personal: "Personal messages", work: "Work messages" };

/* ---------------- keys ---------------- */
function keyLabel(k) {
  const mac = S.platform === "mac";
  const base = { ctrl: mac ? "⌃" : "Ctrl", alt: mac ? "⌥" : "Alt", shift: mac ? "⇧" : "Shift", cmd: mac ? "⌘" : "Win", space: "Space", esc: "Esc", fn: "fn" };
  const m = /^(ctrl|alt|shift|cmd)_(l|r)$/.exec(k);
  if (m) return (m[2] === "r" ? "Right " : "Left ") + base[m[1]];
  return base[k] || k.toUpperCase();
}
const keysHtml = (arr, extra) => '<span class="keys">' + [...arr, ...(extra ? [extra] : [])].map((k) => `<kbd>${esc(keyLabel(k))}</kbd>`).join("") + "</span>";

/* ---------------- toast / modal ---------------- */
function toast(text, kind, action) {
  $$(".toast").forEach((t) => t.remove());
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = `<span class="dot ${kind === "bad" ? "err" : kind === "good" ? "ok" : ""}"></span>${esc(text)}`;
  if (action) {
    const b = document.createElement("button");
    b.className = "btn"; b.type = "button"; b.textContent = action.label;
    b.onclick = () => { t.remove(); action.run(); };
    t.appendChild(b);
  }
  document.body.appendChild(t);
  setTimeout(() => t.remove(), action ? 8000 : 3200);
}
function modal(html, onMount) {
  const m = document.createElement("div");
  m.className = "modal";
  m.innerHTML = `<div class="card mbox" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => m.remove();
  m.addEventListener("mousedown", (e) => { if (e.target === m) close(); });
  m.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  $("#layer").appendChild(m);
  onMount && onMount(m.firstChild, close);
  setTimeout(() => { const f = m.querySelector("input,textarea"); f && f.focus(); }, 30);
  return close;
}
function confirmBox(title, body, okLabel, fn) {
  modal(`<h3>${esc(title)}</h3><p class="note">${esc(body)}</p><div class="mact"><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>${esc(okLabel)}</button></div>`, (box, close) => {
    box.querySelector("[data-x]").onclick = close;
    box.querySelector("[data-ok]").onclick = () => { close(); fn(); };
  });
}

/* ---------------- nav ---------------- */
const TITLES = { home: "Home", notes: "Notetaker", insights: "Insights", dictionary: "Dictionary", snippets: "Snippets", style: "Style", transforms: "Transforms", settings: "Settings" };
function go(page) {
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("show", p.dataset.page === page));
  $("#crumb").textContent = TITLES[page];
  $("#main").scrollTop = 0;
  try { localStorage.setItem("flow.page", page); } catch (e) {}
  ({ home: renderHome, notes: loadNotes, insights: renderInsights, dictionary: loadDict, snippets: loadSnips, style: renderStyle, transforms: loadTransforms, settings: renderSettings }[page] || (() => {}))();
}
$$("#nav a").forEach((a) => a.addEventListener("click", () => go(a.dataset.page)));

// Cursor-following glow on cards
document.addEventListener("mousemove", (e) => {
  const c = e.target.closest && e.target.closest(".card");
  if (!c) return;
  const r = c.getBoundingClientRect();
  c.style.setProperty("--mx", e.clientX - r.left + "px");
  c.style.setProperty("--my", e.clientY - r.top + "px");
});

/* ---------------- header / engines ---------------- */
function greet() {
  const h = new Date().getHours();
  const part = h < 5 ? "Up late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  const name = (S.settings.user_name || "").trim();
  $("#welcome").textContent = name ? `Welcome back, ${name}` : "Welcome back";
  $("#today-line").textContent = `${part} · ${new Date().toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}`;
}
function renderEngines() {
  const st = S.stt || {}, ll = S.llm || {};
  const sd = $("#stt-dot"), ld = $("#llm-dot");
  sd.className = "dot " + ({ ready: "ok", loading: "load", error: "err" }[st.state] || "load");
  $("#stt-line").innerHTML = st.state === "ready" ? `<b>Whisper</b> · ${esc(st.device)}` : st.state === "error" ? "Speech model failed" : "Loading speech model…";
  if (ll.provider === "cloud" && ll.has_key) {
    ld.className = "dot ok";
    $("#llm-line").innerHTML = `<b>${esc(ll.cloud_model)}</b> · cloud`;
  } else {
    const llOk = ll.running && ll.installed;
    ld.className = "dot " + (llOk ? "ok" : ll.running ? "warn" : "err");
    $("#llm-line").innerHTML = llOk ? `<b>${esc(ll.model)}</b>` : ll.running ? `Model ${esc(ll.model)} not downloaded` : "Ollama not running";
  }
  renderLive();
  if ($('.page.show[data-page="settings"]')) renderEngineSettings();
}
function renderLive() {
  const live = $("#live"), dot = $("#live-dot"), txt = $("#live-txt");
  if (!S.settings) return;
  live.classList.toggle("rec", S.recording);
  if (S.recording) { dot.className = "dot rec"; txt.textContent = S.recLocked ? "Hands-free · Esc to cancel" : `Listening${S.recApp ? " · " + S.recApp : ""}`; return; }
  if (S.stt && S.stt.state === "ready") { dot.className = "dot ok ping"; txt.innerHTML = `Ready · hold ${keysHtml(S.settings.dictate_hotkey)}`; }
  else if (S.stt && S.stt.state === "error") { dot.className = "dot err"; txt.textContent = "Speech model unavailable"; }
  else { dot.className = "dot load"; txt.textContent = "Loading speech model…"; }
}

/* ---------------- home ---------------- */
function tile(k, v, d) { return `<div class="card tile"><p class="k">${k}</p><p class="v">${v}</p><p class="d">${d || "&nbsp;"}</p></div>`; }
function renderHome() {
  const st = S.stats; if (!st) return;
  const hk = S.settings.dictate_hotkey;
  $("#hero-lead").innerHTML = `Hold ${keysHtml(hk)} in any app and talk, release and the text appears where your cursor is. Double-tap it for hands-free. Hold ${keysHtml(S.settings.command_hotkey || [])} to give an instruction instead, like "make this shorter".`;
  renderHomeStats();
  loadHistory(true);
}
function renderHomeStats() {
  const st = S.stats;
  $("#hero-words").textContent = fmt(st.words);
  $("#hero-d").innerHTML = st.month_growth != null ? `<span class="pill ${st.month_growth >= 0 ? "good" : "bad"}">${st.month_growth >= 0 ? "↗" : "↘"} ${Math.abs(st.month_growth)}% this month</span>` : "";
  $("#home-tiles").innerHTML =
    tile("Words per minute", st.wpm || "—", st.wpm ? `<span class="pill plain">${(st.wpm / 40).toFixed(1)}× typing speed</span>` : "Dictate to measure") +
    tile("Day streak", st.streak, `Longest ${st.longest_streak} day${st.longest_streak === 1 ? "" : "s"}`) +
    tile("Fixes made by Hush", fmt(st.fixes), `${fmt(st.word_fixes)} words · ${fmt(st.dict_fixes)} dictionary`) +
    tile("Dictations", fmt(st.count), timeSaved(st.words));
}
function timeSaved(words) {
  // vs typing at 40 wpm
  const mins = Math.round(words / 40 - words / Math.max(S.stats.wpm || 120, 60));
  return mins > 0 ? `≈ ${mins >= 60 ? (mins / 60).toFixed(1) + " h" : mins + " min"} saved vs typing` : "&nbsp;";
}
async function loadHistory(reset) {
  if (reset) S.hist = [];
  const rows = await api.list_dictations(S.histQ, S.hist.length, 60);
  S.hist = S.hist.concat(rows);
  $("#hist-more").hidden = rows.length < 60;
  renderHistory();
}
function dayLabel(ts) {
  const d = new Date(ts * 1000), t = new Date();
  const y = new Date(); y.setDate(t.getDate() - 1);
  if (d.toDateString() === t.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { month: "long", day: "numeric", year: d.getFullYear() === t.getFullYear() ? undefined : "numeric" });
}
const timeLabel = (ts) => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).replace(" ", "").toLowerCase();
function renderHistory(freshId) {
  const el = $("#history");
  if (!S.hist.length) {
    el.innerHTML = S.histQ ? `<div class="empty"><b>No matches</b>Nothing in your history matches “${esc(S.histQ)}”.</div>`
      : `<div class="empty"><b>Nothing here yet</b><span>Hold ${keysHtml(S.settings.dictate_hotkey)} anywhere and start talking.</span></div>`;
    return;
  }
  let html = "", day = null;
  for (const r of S.hist) {
    const dl = dayLabel(r.ts);
    if (dl !== day) { if (day !== null) html += "</div>"; html += `<div class="day">${esc(dl)}</div><div class="hist">`; day = dl; }
    const meta = [r.app ? `<span class="pill plain">${esc(r.app)}</span>` : "", r.transform ? `<span class="pill info">${esc(r.transform.replace(/_/g, " "))}</span>` : ""].join("");
    html += `<div class="hrow ${r.flagged ? "flagged" : ""} ${r.id === freshId ? "fresh" : ""}" data-id="${r.id}">
      <div class="t">${timeLabel(r.ts)}</div>
      <div><div class="x">${esc(r.text)}</div>${meta ? `<div class="meta">${meta}</div>` : ""}</div>
      <div class="acts">
        ${r.audio ? `<button class="icon-btn" data-a="play" title="Play audio">${ICON.play}</button>` : ""}
        <button class="icon-btn" data-a="copy" title="Copy">${ICON.copy}</button>
        <button class="icon-btn" data-a="raw" title="Compare with raw transcript">${ICON.redo}</button>
        <button class="icon-btn ${r.flagged ? "on" : ""}" data-a="flag" title="Flag">${ICON.flag}</button>
        <button class="icon-btn danger" data-a="del" title="Delete">${ICON.trash}</button>
      </div></div>`;
  }
  el.innerHTML = html + "</div>";
}
let player = null;
$("#history").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-a]"); if (!b) return;
  const row = b.closest(".hrow"), id = +row.dataset.id, r = S.hist.find((x) => x.id === id);
  const a = b.dataset.a;
  if (a === "copy") { await api.copy_text(r.text); toast("Copied to clipboard", "good"); }
  if (a === "flag") { await api.flag_dictation(id); r.flagged = r.flagged ? 0 : 1; renderHistory(); }
  if (a === "del") { S.stats = await api.delete_dictation(id); S.hist = S.hist.filter((x) => x.id !== id); renderHistory(); renderHomeStats(); }
  if (a === "play") {
    if (player && player.dataset.id == id && !player.paused) { player.pause(); b.innerHTML = ICON.play; return; }
    if (player) player.pause();
    const src = await api.get_audio(id);
    if (!src) return toast("Audio file not found", "bad");
    player = new Audio(src); player.dataset.id = id; player.play();
    b.innerHTML = ICON.pause; player.onended = player.onpause = () => (b.innerHTML = ICON.play);
  }
  if (a === "raw") {
    modal(`<h3>Raw vs. cleaned</h3><div class="field">What Whisper heard<div class="out">${esc(r.raw)}</div></div><div class="field">What Hush typed<div class="out">${esc(r.text)}</div></div>
      <div class="mact"><button class="btn" data-x>Close</button><button class="btn" data-copyraw>Copy raw</button><button class="btn primary" data-retry>Re-run cleanup</button></div>`, (box, close) => {
      box.querySelector("[data-x]").onclick = close;
      box.querySelector("[data-copyraw]").onclick = async () => { await api.copy_text(r.raw); toast("Raw transcript copied", "good"); };
      box.querySelector("[data-retry]").onclick = async (ev) => {
        ev.target.disabled = true; ev.target.textContent = "Cleaning…";
        try { const t = await api.retry_dictation(id); box.querySelectorAll(".out")[1].textContent = t; await api.copy_text(t); toast("New version copied", "good"); }
        catch (err) { toast(String(err), "bad"); }
        ev.target.disabled = false; ev.target.textContent = "Re-run cleanup";
      };
    });
  }
});
let qTimer;
$("#hist-q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { S.histQ = e.target.value.trim(); loadHistory(true); }, 200); });
$("#hist-more").addEventListener("click", () => loadHistory(false));
$("#tryit-clear").addEventListener("click", () => { $("#tryit").value = ""; $("#tryit").focus(); });

/* hero wave: layered chrome lines that swell with the mic level */
(function wave() {
  const cv = $("#wave"), ctx = cv.getContext("2d");
  let t = 0, amp = 0.18;
  function frame() {
    const page = $('.page.show[data-page="home"]');
    if (page && document.visibilityState === "visible") {
      const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
      if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const target = S.recording ? 0.35 + S.level * 1.6 : 0.16;
      amp += (target - amp) * 0.08;
      t += S.recording ? 0.045 : 0.012;
      for (let L = 0; L < 6; L++) {
        const g = ctx.createLinearGradient(0, 0, w, 0);
        const a = 0.08 + (L === 0 ? 0.5 : 0.25 / (L + 1));
        const tint = S.recording ? `rgba(255,${190 - L * 10},${195 - L * 10},` : "rgba(255,255,255,";
        g.addColorStop(0, tint + "0)"); g.addColorStop(0.5, tint + a + ")"); g.addColorStop(1, tint + "0)");
        ctx.strokeStyle = g; ctx.lineWidth = L === 0 ? 1.6 : 1;
        if (L === 0) { ctx.shadowColor = "rgba(255,255,255,0.6)"; ctx.shadowBlur = 10; } else ctx.shadowBlur = 0;
        ctx.beginPath();
        for (let x = 0; x <= w; x += 4) {
          const p = x / w, env = Math.sin(Math.PI * p);
          const y = h * 0.58 + Math.sin(p * (6 + L * 0.7) + t * (1 + L * 0.15) + L) * h * amp * env * (1 - L * 0.1)
            + Math.sin(p * 17 + t * 2.3 + L * 2) * h * 0.03 * env * (S.recording ? 1 + S.level * 3 : 1);
          x ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        }
        ctx.stroke();
      }
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();

/* ---------------- notetaker ---------------- */
for (let i = 0; i < 48; i++) $("#note-meter").appendChild(document.createElement("i"));
let noteTick = null;
function setNoteUI(on, started, systemAudio) {
  const btn = $("#note-btn");
  btn.classList.toggle("on", on);
  btn.setAttribute("aria-label", on ? "Stop recording" : "Start recording");
  $("#note-meter").classList.toggle("on", on);
  $("#note-badge").hidden = !on;
  $("#note-state").textContent = on ? `Recording ${systemAudio ? "you + computer audio" : "your mic"} · click to stop and summarize` : "Ready to record";
  syncNoteSettings();
  clearInterval(noteTick);
  if (on) {
    const t0 = started * 1000;
    const upd = () => { const s = Math.floor((Date.now() - t0) / 1000); $("#note-timer").textContent = `${s >= 3600 ? Math.floor(s / 3600) + ":" : ""}${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
    upd(); noteTick = setInterval(upd, 500);
  } else {
    $("#note-timer").textContent = "00:00";
    $$("#note-meter i").forEach((b) => (b.style.height = "4px"));
  }
}
$("#note-btn").addEventListener("click", async () => {
  if ($("#note-btn").classList.contains("on")) {
    setNoteUI(false);
    const id = await api.stop_note();
    await loadNotes();
    if (id) selectNote(id);
  } else {
    const r = await api.start_note();
    if (!r.ok) return toast(r.error || "Couldn't start recording", "bad");
    setNoteUI(true, r.started, r.system_audio);
  }
});
async function loadNotes() { S.notes = await api.list_notes(); renderNotes(); syncNoteSettings(); }
function syncNoteSettings() {
  $("#sysaudio-row").hidden = S.platform !== "win";  // macOS can't capture system audio without an extra driver
  $("#sysaudio-row .switch").setAttribute("aria-checked", !!(S.settings && S.settings.note_system_audio));
}
function transcriptHtml(t) {
  // "Me: ..." / "Them: ..." turns from a call get speaker labels; older mic-only notes stay plain text
  if (!/^(Me|Them): /m.test(t)) return esc(t);
  return t.split(/\n{2,}/).map((p) => {
    const m = /^(Me|Them): ([\s\S]*)$/.exec(p.trim());
    return m ? `<div class="turn ${m[1].toLowerCase()}"><span class="who">${m[1]}</span><span>${esc(m[2])}</span></div>` : `<div class="turn">${esc(p)}</div>`;
  }).join("");
}
function renderNotes() {
  const q = ($("#notes-q").value || "").toLowerCase();
  const list = S.notes.filter((n) => !q || (n.title || "").toLowerCase().includes(q) || (n.summary || "").toLowerCase().includes(q));
  if (!list.length) { $("#notes-list").innerHTML = `<div class="empty"><b>${q ? "No matches" : "No notes yet"}</b>${q ? "" : "Press record when your meeting starts."}</div>`; return; }
  let html = "", day = null;
  for (const n of list) {
    const dl = dayLabel(n.ts);
    if (dl !== day) { html += `<div class="day">${esc(dl)}</div>`; day = dl; }
    const mins = Math.max(1, Math.round(n.duration / 60));
    const status = n.status === "processing" ? ' · <span style="color:var(--warn)">processing</span>' : n.status === "error" ? ' · <span style="color:var(--bad)">failed</span>' : "";
    html += `<button class="nitem ${S.noteSel === n.id ? "on" : ""}" data-id="${n.id}" type="button"><span class="av">${esc((n.title || "U")[0].toUpperCase())}</span><span style="min-width:0"><b>${esc(n.title || "Untitled")}</b><small>${timeLabel(n.ts)} · ${mins} min${status}</small></span></button>`;
  }
  $("#notes-list").innerHTML = html;
}
$("#notes-q").addEventListener("input", renderNotes);
$("#notes-list").addEventListener("click", (e) => { const b = e.target.closest(".nitem"); if (b) selectNote(+b.dataset.id); });
async function selectNote(id) {
  S.noteSel = id; renderNotes();
  const n = await api.get_note(id); if (!n) return;
  const el = $("#note-detail");
  let data = {}; try { data = JSON.parse(n.summary || "{}"); } catch (e) { data = { overview: n.summary }; }
  if (n.status === "processing") {
    el.innerHTML = `<div class="head"><h3>${esc(n.title)}</h3></div><p class="note" id="np-txt">Transcribing…</p><div class="progress"><i id="np-bar" style="width:2%"></i></div>`;
    return;
  }
  const list = (arr) => (arr && arr.length ? `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="note">None</p>');
  el.innerHTML = `<div class="head"><div style="min-width:0"><h3 contenteditable="true" spellcheck="false" id="nt-title">${esc(n.title || "Untitled")}</h3>
      <p class="note">${new Date(n.ts * 1000).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · ${Math.max(1, Math.round(n.duration / 60))} min</p></div>
      <div class="status"><button class="icon-btn" data-n="copy" title="Copy notes">${ICON.copy}</button><button class="icon-btn danger" data-n="del" title="Delete">${ICON.trash}</button></div></div>
    <div class="lbl">Overview</div><p style="margin:0">${esc(data.overview || "—")}</p>
    ${data.key_points && data.key_points.length ? `<div class="lbl">Key points</div>${list(data.key_points)}` : ""}
    <div class="lbl">Action items</div>${list(data.action_items)}
    ${n.transcript ? `<details><summary class="note" style="cursor:pointer">Full transcript · ${fmt(n.transcript.split(/\s+/).length)} words</summary><div class="transcript" style="margin-top:10px">${transcriptHtml(n.transcript)}</div></details>` : ""}`;
  const title = $("#nt-title");
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } });
  title.addEventListener("blur", async () => { await api.rename_note(id, title.textContent); loadNotes(); });
  el.querySelector('[data-n="copy"]').onclick = async () => {
    const txt = `${n.title}\n\n${data.overview || ""}\n\n${(data.key_points || []).map((x) => "- " + x).join("\n")}\n\nAction items:\n${(data.action_items || []).map((x) => "- " + x).join("\n") || "- None"}`;
    await api.copy_text(txt); toast("Notes copied", "good");
  };
  el.querySelector('[data-n="del"]').onclick = () => confirmBox("Delete this note?", "The transcript, summary and audio are removed permanently.", "Delete", async () => {
    S.notes = await api.delete_note(id); S.noteSel = null; renderNotes();
    el.innerHTML = '<div class="empty"><b>No note selected</b>Pick a note on the left, or record a new one.</div>';
  });
}

/* ---------------- insights ---------------- */
/* ---- weekly recap ---- */
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
S.recapWeek = 0;
async function loadRecap() { renderRecap(await api.get_recap(S.recapWeek)); }
function renderRecap(r) {
  const d = (iso) => new Date(iso + "T12:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const mins = (m) => (m >= 60 ? `${(m / 60).toFixed(1)} h` : `${Math.round(m)} min`);
  const change = r.change_pct == null ? "" :
    `<span class="pill ${r.change_pct >= 0 ? "good" : "bad"}">${r.change_pct >= 0 ? "↗" : "↘"} ${Math.abs(r.change_pct)}% vs ${r.this_week ? "same time last week" : "the week before"}</span>`;
  const max = Math.max(1, ...r.by_day);
  const todayIdx = (new Date().getDay() + 6) % 7;
  const bars = r.by_day.map((w, i) => `<div class="rday ${r.this_week && i === todayIdx ? "today" : ""}" title="${DAYS[i]}: ${fmt(w)} words">
      <div class="rbar"><i style="height:${Math.max(w ? 6 : 2, (w / max) * 100)}%;${w ? "" : "opacity:.25"}"></i></div><span>${DAYS[i][0]}</span></div>`).join("");
  const apps = r.top_apps.length ? r.top_apps.map((a, i) => `<div class="kv"><span>${i + 1}. ${esc(a.app)}</span><b>${fmt(a.words)} words</b></div>`).join("")
    : '<p class="note">No dictations yet.</p>';
  const highlights = [
    r.busiest_day != null && r.by_day[r.busiest_day] ? `Busiest day: <b>${DAYS[r.busiest_day]}</b> (${fmt(r.by_day[r.busiest_day])} words)` : "",
    r.commands ? `<b>${r.commands}</b> command${r.commands === 1 ? "" : "s"} used` : "",
    r.fixes ? `<b>${fmt(r.fixes)}</b> fixes by Hush` : "",
    r.learned.length ? `Learned: ${r.learned.slice(0, 5).map((w) => `<span class="pill plain">${esc(w)}</span>`).join(" ")}${r.learned.length > 5 ? ` +${r.learned.length - 5}` : ""}` : "",
  ].filter(Boolean);
  $("#recap").innerHTML = `
    <div class="head"><div><h3>${r.this_week ? "This week" : "Last week"}</h3><p class="note">${d(r.start)} – ${d(r.end)}</p></div>
      <div class="seg" id="recap-seg"><button type="button" data-w="0" aria-pressed="${r.this_week}">This week</button><button type="button" data-w="1" aria-pressed="${!r.this_week}">Last week</button></div></div>
    <div class="recap">
      <div class="rhero"><div class="big">${fmt(r.words)}</div><p class="note">words dictated · ${fmt(r.dictations)} dictations</p>${change}</div>
      <div class="rstats">
        <div class="kv"><span>Time talking</span><b>${mins(r.speaking_min)}</b></div>
        <div class="kv"><span>Time saved vs typing</span><b>${r.saved_min ? "≈ " + mins(r.saved_min) : "—"}</b></div>
        ${highlights.map((h) => `<p class="note rhl">${h}</p>`).join("")}
      </div>
      <div class="rdays">${bars}</div>
      <div class="rapps"><p class="note" style="margin-bottom:4px">Top apps</p>${apps}</div>
    </div>`;
  $("#recap-seg").onclick = (e) => { const b = e.target.closest("button"); if (b) { S.recapWeek = +b.dataset.w; loadRecap(); } };
}

function renderInsights() {
  const st = S.stats; if (!st) return;
  loadRecap();
  const pct = Math.min(1, (st.wpm || 0) / 180);
  const R = 62, C = Math.PI * R;
  const equiv = [[50000, "a short novel"], [20000, "a thesis chapter"], [10000, "an instruction manual"], [5000, "a long-form article"], [1500, "a college essay"], [300, "a cover letter"], [50, "a tweet thread"]].find(([n]) => st.words >= n);
  $("#ins-top").innerHTML = `
    <section class="card"><div class="head"><h3>Words per minute</h3><span class="pill plain">${st.wpm ? (st.wpm / 40).toFixed(1) + "× typing" : "—"}</span></div>
      <div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap">
        <div class="gauge"><svg viewBox="0 0 150 86"><path d="M13 80a62 62 0 0 1 124 0" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="10" stroke-linecap="round"/>
          <path class="arc" d="M13 80a62 62 0 0 1 124 0" fill="none" stroke="url(#gg)" stroke-width="10" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C}" data-off="${C * (1 - pct)}"/>
          <defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stop-color="#55555E"/><stop offset="1" stop-color="#fff"/></linearGradient></defs></svg>
          <div class="lab"><b>${st.wpm || "—"}</b><span>wpm</span></div></div>
        <p class="note" style="flex:1;min-width:120px">Average typing speed is about 40 wpm. Measured over ${fmt(Math.round(st.words / Math.max(st.wpm, 1)))} minutes of speech.</p></div></section>
    <section class="card"><div class="head"><h3>Fixes made by Hush</h3></div><div class="big">${fmt(st.fixes)}</div>
      <div><div class="kv"><span>Words corrected by cleanup</span><b>${fmt(st.word_fixes)}</b></div><div class="kv"><span>Dictionary fixes</span><b>${fmt(st.dict_fixes)}</b></div></div></section>
    <section class="card"><div class="head"><h3>Total words dictated</h3>${st.month_growth != null ? `<span class="pill ${st.month_growth >= 0 ? "good" : "bad"}">${st.month_growth >= 0 ? "↗" : "↘"} ${Math.abs(st.month_growth)}% this month</span>` : ""}</div>
      <div class="big">${fmt(st.words)}</div><p class="note">${equiv ? `You've written ${equiv[1]}.` : "Your first words are on their way."}</p>
      <div><div class="kv"><span>Dictations</span><b>${fmt(st.count)}</b></div><div class="kv"><span>Avg words per dictation</span><b>${st.count ? Math.round(st.words / st.count) : 0}</b></div></div></section>`;
  requestAnimationFrame(() => $$(".gauge .arc").forEach((a) => (a.style.strokeDashoffset = a.dataset.off)));

  const cats = Object.entries(st.categories), total = Math.max(1, cats.reduce((s, [, n]) => s + n, 0));
  cats.sort((a, b) => b[1] - a[1]);
  const usage = cats.map(([c, n]) => `<div class="row">${ICON[c]}<span>${CAT_LABEL[c]}</span><span class="a">${fmt(n)} · ${Math.round((n / total) * 100)}%</span><div class="bar"><i style="width:${(n / total) * 100}%"></i></div></div>`).join("");

  // 26-week heatmap ending this week
  const days = st.days || {};
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const start = new Date(today); start.setDate(today.getDate() - today.getDay() - 25 * 7);
  const vals = Object.values(days), max = Math.max(1, ...vals);
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  let heat = `<span></span>${["Sun", "", "Tue", "", "Thu", "", "Sat"].map((d) => `<span class="dl">${d}</span>`).join("")}`;
  let lastMonth = -1;
  for (let w = 0; w < 26; w++) {
    const col = new Date(start); col.setDate(start.getDate() + w * 7);
    const m = col.getMonth();
    heat += `<span class="m">${m !== lastMonth ? col.toLocaleDateString(undefined, { month: "short" }) : ""}</span>`; lastMonth = m;
    for (let d = 0; d < 7; d++) {
      const day = new Date(col); day.setDate(col.getDate() + d);
      if (day > today) { heat += '<span class="c" style="visibility:hidden"></span>'; continue; }
      const v = days[iso(day)] || 0, lvl = v ? Math.min(4, 1 + Math.floor((v / max) * 3.999)) : 0;
      heat += `<span class="c ${lvl ? "l" + lvl : ""} ${day.getTime() === today.getTime() ? "today" : ""}" title="${day.toDateString()}: ${fmt(v)} words"></span>`;
    }
  }
  $("#ins-bottom").innerHTML = `
    <section class="card"><div class="head"><h3>Where you dictate</h3><span class="note">${fmt(total === 1 && !st.count ? 0 : total)} dictations</span></div><div class="rows">${usage}</div></section>
    <section class="card"><div class="head"><h3>${st.streak} day streak</h3><span class="note">Longest ${st.longest_streak} days</span></div>
      <div class="heatwrap"><div class="heat">${heat}</div></div>
      <div class="hlegend">Less <span class="c" style="background:rgba(255,255,255,0.05)"></span><span class="c" style="background:rgba(255,255,255,0.18)"></span><span class="c" style="background:rgba(255,255,255,0.38)"></span><span class="c" style="background:rgba(255,255,255,0.62)"></span><span class="c" style="background:#fff"></span> More</div></section>`;
}

/* ---------------- dictionary ---------------- */
async function loadDict() { S.dict = await api.list_dictionary(); renderDict(); }
function renderDict() {
  const q = ($("#dict-q").value || "").toLowerCase();
  const list = S.dict.filter((d) => !q || d.phrase.toLowerCase().includes(q) || (d.replacement || "").toLowerCase().includes(q));
  $("#dict-list").innerHTML = list.length ? `<div class="list">${list.map((d) => `<div class="li" data-id="${d.id}"><div class="main">${d.starred ? `<span class="star">${ICON.star.replace("<svg", '<svg width="14" height="14" fill="currentColor" stroke="none"')}</span>` : ""}<span>${esc(d.phrase)}</span>${d.replacement ? `<span class="arrow">→</span><span>${esc(d.replacement)}</span>` : ""}${d.auto ? '<span class="learned" title="Learned from one of your corrections">✨</span>' : ""}</div>
    <div class="acts"><button class="icon-btn" data-a="edit" title="Edit">${ICON.edit}</button><button class="icon-btn danger" data-a="del" title="Delete">${ICON.trash}</button><button class="icon-btn ${d.starred ? "on" : ""}" data-a="star" title="Star">${ICON.star}</button></div></div>`).join("")}</div>`
    : `<div class="empty"><b>${q ? "No matches" : "Your dictionary is empty"}</b>${q ? "" : "Add names and terms Whisper keeps getting wrong."}</div>`;
}
$("#dict-q").addEventListener("input", renderDict);
function dictModal(d) {
  modal(`<h3>${d ? "Edit word" : "Add to dictionary"}</h3>
    <label class="field">Word or phrase<input class="tin" id="m-phrase" value="${esc(d ? d.phrase : "")}" placeholder="e.g. PIXELFORGE"><small>Spelled exactly how you want it to appear.</small></label>
    <label class="chk setrow" style="padding:0;border:0"><span class="lbl"><b>Replace with something else</b><small>Turn a spoken phrase into different text, e.g. btw → by the way.</small></span><button class="switch" id="m-rep-on" role="switch" aria-checked="${d && d.replacement ? "true" : "false"}" type="button"></button></label>
    <label class="field" id="m-rep-wrap" ${d && d.replacement ? "" : "hidden"}>Replacement<input class="tin" id="m-rep" value="${esc(d && d.replacement ? d.replacement : "")}" placeholder="by the way"></label>
    <div class="mact"><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>${d ? "Save" : "Add word"}</button></div>`, (box, close) => {
    const sw = box.querySelector("#m-rep-on");
    sw.onclick = () => { const on = sw.getAttribute("aria-checked") !== "true"; sw.setAttribute("aria-checked", on); box.querySelector("#m-rep-wrap").hidden = !on; };
    box.querySelector("[data-x]").onclick = close;
    const save = async () => {
      const phrase = box.querySelector("#m-phrase").value.trim(); if (!phrase) return;
      const rep = sw.getAttribute("aria-checked") === "true" ? box.querySelector("#m-rep").value.trim() : "";
      S.dict = d ? await api.update_dictionary(d.id, phrase, rep) : await api.add_dictionary(phrase, rep);
      renderDict(); close();
    };
    box.querySelector("[data-ok]").onclick = save;
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
  });
}
$("#dict-add").addEventListener("click", () => dictModal(null));
$("#dict-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-a]"); if (!b) return;
  const id = +b.closest(".li").dataset.id, d = S.dict.find((x) => x.id === id);
  if (b.dataset.a === "edit") dictModal(d);
  if (b.dataset.a === "del") { S.dict = await api.delete_dictionary(id); renderDict(); }
  if (b.dataset.a === "star") { S.dict = await api.star_dictionary(id); renderDict(); }
});

/* ---------------- snippets ---------------- */
async function loadSnips() { S.snips = await api.list_snippets(); renderSnips(); }
function renderSnips() {
  const q = ($("#snip-q").value || "").toLowerCase();
  const list = S.snips.filter((s) => !q || s.trigger.toLowerCase().includes(q) || s.expansion.toLowerCase().includes(q));
  $("#snip-list").innerHTML = list.length ? `<div class="list">${list.map((s) => `<div class="li" data-id="${s.id}"><div class="main"><span>${esc(s.trigger)}</span><span class="arrow">→</span><span class="exp">${esc(s.expansion)}</span></div>
    <div class="acts"><button class="icon-btn" data-a="copy" title="Copy expansion">${ICON.copy}</button><button class="icon-btn" data-a="edit" title="Edit">${ICON.edit}</button><button class="icon-btn danger" data-a="del" title="Delete">${ICON.trash}</button></div></div>`).join("")}</div>`
    : `<div class="empty"><b>${q ? "No matches" : "No snippets yet"}</b>${q ? "" : "Save your links, addresses and boilerplate."}</div>`;
}
$("#snip-q").addEventListener("input", renderSnips);
function snipModal(s) {
  modal(`<h3>${s ? "Edit snippet" : "New snippet"}</h3>
    <label class="field">Say this<input class="tin" id="m-trig" value="${esc(s ? s.trigger : "")}" placeholder="my LinkedIn"><small>A short, distinctive phrase you wouldn't say by accident.</small></label>
    <label class="field">Hush types this<textarea class="tin" id="m-exp" placeholder="https://www.linkedin.com/in/…">${esc(s ? s.expansion : "")}</textarea></label>
    <div class="mact"><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>${s ? "Save" : "Add snippet"}</button></div>`, (box, close) => {
    box.querySelector("[data-x]").onclick = close;
    box.querySelector("[data-ok]").onclick = async () => {
      const t = box.querySelector("#m-trig").value.trim(), x = box.querySelector("#m-exp").value;
      if (!t || !x.trim()) return;
      S.snips = s ? await api.update_snippet(s.id, t, x) : await api.add_snippet(t, x);
      renderSnips(); close();
    };
  });
}
$("#snip-add").addEventListener("click", () => snipModal(null));
$("#snip-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-a]"); if (!b) return;
  const id = +b.closest(".li").dataset.id, s = S.snips.find((x) => x.id === id);
  if (b.dataset.a === "edit") snipModal(s);
  if (b.dataset.a === "copy") { await api.copy_text(s.expansion); toast("Copied", "good"); }
  if (b.dataset.a === "del") { S.snips = await api.delete_snippet(id); renderSnips(); }
});

/* ---------------- style ---------------- */
const STYLE_INFO = {
  personal: { h: "Personal messengers", sub: "Applies in chat apps with friends and family.", apps: ["WhatsApp", "Telegram", "Discord", "Instagram", "Messages", "Signal"], sample: ["Hey, are you free for lunch tomorrow? Let's do 12 if that works for you.", "Hey are you free for lunch tomorrow? Let's do 12 if that works for you", "hey are you free for lunch tomorrow? let's do 12 if that works for you"] },
  work: { h: "Work messengers", sub: "Applies in team chat.", apps: ["Slack", "Teams", "Zoom", "Linear"], sample: ["Hey, the deck is ready for review. Can you take a look before 3?", "Hey the deck is ready for review. Can you take a look before 3?", "hey the deck is ready for review. can you take a look before 3?"] },
  email: { h: "Email", sub: "Applies in mail apps and webmail.", apps: ["Gmail", "Outlook", "Apple Mail", "Superhuman"], sample: ["Hi Sam, thanks for the update. I'll send the contract over today.", "Hi Sam thanks for the update. I'll send the contract over today", "hi sam thanks for the update. i'll send the contract over today"] },
  other: { h: "Everywhere else", sub: "Documents, AI prompts, code editors and any other app.", apps: ["ChatGPT", "Claude", "Notion", "Word", "Cursor"], sample: ["Write a summary of this report and list the three biggest risks.", "Write a summary of this report and list the three biggest risks", "write a summary of this report and list the three biggest risks"] },
};
const STYLES = [["formal", "Formal.", "Caps + Punctuation"], ["casual", "Casual", "Caps + Less punctuation"], ["very_casual", "very casual", "No Caps + Less punctuation"]];
function renderStyle() {
  const c = S.styleCat, info = STYLE_INFO[c], cur = S.settings.styles[c] || "formal";
  $$("#style-tabs button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.c === c));
  $("#style-h").textContent = info.h; $("#style-sub").textContent = info.sub;
  $("#style-apps").innerHTML = info.apps.map((a) => `<span class="chip">${esc(a)}</span>`).join("");
  const initial = ((S.settings.user_name || "J")[0] || "J").toUpperCase();
  $("#style-cards").innerHTML = STYLES.map(([k, name, sub], i) => `<button class="card scard ${cur === k ? "on" : ""}" data-s="${k}" type="button" aria-pressed="${cur === k}"><span class="radio"></span><h4>${name}</h4><p class="note">${sub}</p><div class="bubble">${esc(info.sample[i])}</div><span class="who">${esc(initial)}</span></button>`).join("");
  $("#sw-cleanup").setAttribute("aria-checked", !!S.settings.auto_cleanup);
  loadRules();
}

/* ---- app rules ---- */
const RULE_EXAMPLES = [
  ["Slack", "Never end a message with a period."],
  ["Gmail", "Always end with the sign-off 'Best, Alex' on its own line."],
  ["Cursor", "Wrap code names like function, file and variable names in backticks."],
  ["Messages", "Write everything in lowercase."],
];
async function loadRules() { const r = await api.list_rules(); S.rules = r.rules; S.ruleApps = r.apps; renderRules(); }
function renderRules() {
  const list = S.rules || [];
  $("#rules-list").innerHTML = list.length ? `<div class="list">${list.map((r) => `<div class="li" data-id="${r.id}" style="${r.enabled ? "" : "opacity:.5"}">
      <div class="main"><span class="pill plain">${esc(r.app)}</span><span class="exp" style="white-space:normal">${esc(r.instruction)}</span></div>
      <button class="switch" data-a="toggle" role="switch" aria-checked="${!!r.enabled}" aria-label="Rule on or off"></button>
      <div class="acts"><button class="icon-btn" data-a="edit" title="Edit">${ICON.edit}</button><button class="icon-btn danger" data-a="del" title="Delete">${ICON.trash}</button></div></div>`).join("")}</div>`
    : `<div class="empty"><b>No app rules yet</b><span>For example: ${RULE_EXAMPLES.slice(0, 2).map(([a, i]) => `<span class="pill plain">${a}</span> ${esc(i)}`).join(" · ")}</span></div>`;
}
function ruleModal(r) {
  const apps = [...new Set([...(S.ruleApps || []), ...RULE_EXAMPLES.map((e) => e[0]), "Gmail", "ChatGPT", "Claude", "Notion", "Word"])];
  modal(`<h3>${r ? "Edit rule" : "New app rule"}</h3>
    <label class="field">App or site<input class="tin" id="m-app" list="m-apps" value="${esc(r ? r.app : "")}" placeholder="Slack, Gmail, Cursor…"><datalist id="m-apps">${apps.map((a) => `<option value="${esc(a)}">`).join("")}</datalist><small>Matches the app's name, or the site in a browser tab ("gmail", "linkedin").</small></label>
    <label class="field">Instruction<textarea class="tin" id="m-inst" placeholder="Never end a message with a period.">${esc(r ? r.instruction : "")}</textarea></label>
    ${r ? "" : `<div class="examples"><span class="note">Examples:</span>${RULE_EXAMPLES.map(([a, i], k) => `<button class="chip" type="button" data-ex="${k}">${esc(a)}: ${esc(i.length > 34 ? i.slice(0, 32) + "…" : i)}</button>`).join("")}</div>`}
    <div class="mact"><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>${r ? "Save" : "Add rule"}</button></div>`, (box, close) => {
    box.querySelector("[data-x]").onclick = close;
    box.querySelectorAll("[data-ex]").forEach((b) => b.onclick = () => {
      const [a, i] = RULE_EXAMPLES[+b.dataset.ex]; box.querySelector("#m-app").value = a; box.querySelector("#m-inst").value = i;
    });
    box.querySelector("[data-ok]").onclick = async () => {
      const app = box.querySelector("#m-app").value.trim(), inst = box.querySelector("#m-inst").value.trim();
      if (!app || !inst) return;
      const res = r ? await api.update_rule(r.id, app, inst, !!r.enabled) : await api.add_rule(app, inst);
      S.rules = res.rules; S.ruleApps = res.apps; renderRules(); close();
    };
  });
}
$("#rule-add").addEventListener("click", () => ruleModal(null));
$("#rules-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-a]"); if (!b) return;
  const id = +b.closest(".li").dataset.id, r = S.rules.find((x) => x.id === id);
  let res;
  if (b.dataset.a === "edit") return ruleModal(r);
  if (b.dataset.a === "del") res = await api.delete_rule(id);
  if (b.dataset.a === "toggle") res = await api.update_rule(id, r.app, r.instruction, !r.enabled);
  if (res) { S.rules = res.rules; S.ruleApps = res.apps; renderRules(); }
});
$("#style-tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { S.styleCat = b.dataset.c; renderStyle(); } });
$("#style-cards").addEventListener("click", async (e) => {
  const b = e.target.closest(".scard"); if (!b) return;
  S.settings = await api.set_settings({ styles: { [S.styleCat]: b.dataset.s } });
  renderStyle();
});
$("#sw-cleanup").addEventListener("click", async () => { S.settings = await api.set_settings({ auto_cleanup: !S.settings.auto_cleanup }); renderStyle(); });

/* ---------------- transforms ---------------- */
async function loadTransforms() { S.tfs = await api.list_transforms(); renderTransforms(); }
function renderTransforms() {
  const mods = S.settings.transform_modifiers;
  $("#tf-cards").innerHTML = S.tfs.map((t) => `<button class="card tcard" data-id="${esc(t.id)}" type="button">${t.slot ? keysHtml(mods, String(t.slot)) : '<span class="pill plain">No shortcut</span>'}<h4>${esc(t.name)}</h4><p>${esc(t.description || "")}</p></button>`).join("")
    + `<button class="card tcard new" id="tf-new2" type="button"><span class="plus">+</span><h4>Create your own</h4><p>Write your own prompt</p></button>`;
  const opts = S.tfs.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  $("#tf-auto-sel").innerHTML = opts; $("#tf-try-sel").innerHTML = opts;
  const at = S.settings.auto_transform;
  $("#tf-auto-sel").value = at.transform_id;
  $("#sw-autotf").setAttribute("aria-checked", !!at.enabled);
}
function tfModal(t) {
  const builtin = t && t.builtin;
  modal(`<h3>${t ? "Edit transform" : "Create a transform"}</h3>
    <label class="field">Name<input class="tin" id="m-name" value="${esc(t ? t.name : "")}" placeholder="e.g. Make it friendly"></label>
    <label class="field">Description<input class="tin" id="m-desc" value="${esc(t ? t.description : "")}" placeholder="Shown on the card"></label>
    <label class="field">Instruction for the model<textarea class="tin" id="m-prompt" style="min-height:150px" placeholder="Rewrite the text so it…">${esc(t ? t.prompt : "")}</textarea><small>Describe the rewrite. Hush handles the rest (it never answers the text, only rewrites it).</small></label>
    <div class="mact">${t && !builtin ? '<button class="btn danger" data-del>Delete</button>' : ""}<span class="sp"></span><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>${t ? "Save" : "Create"}</button></div>`, (box, close) => {
    box.querySelector("[data-x]").onclick = close;
    const del = box.querySelector("[data-del]");
    if (del) del.onclick = async () => { S.tfs = await api.delete_transform(t.id); renderTransforms(); close(); };
    box.querySelector("[data-ok]").onclick = async () => {
      const name = box.querySelector("#m-name").value.trim(), prompt = box.querySelector("#m-prompt").value.trim();
      if (!name || !prompt) return;
      S.tfs = await api.save_transform(t ? t.id : "", name, box.querySelector("#m-desc").value, prompt);
      renderTransforms(); close();
    };
  });
}
$("#tf-cards").addEventListener("click", (e) => {
  const b = e.target.closest(".tcard"); if (!b) return;
  if (b.id === "tf-new2") return tfModal(null);
  tfModal(S.tfs.find((t) => t.id === b.dataset.id));
});
$("#tf-new").addEventListener("click", () => tfModal(null));
$("#tf-reset").addEventListener("click", () => confirmBox("Reset transforms?", "Your custom transforms are removed and Polish and Prompt Engineer are restored.", "Reset", async () => { S.tfs = await api.reset_transforms(); renderTransforms(); }));
$("#tf-auto-sel").addEventListener("change", async (e) => { S.settings = await api.set_settings({ auto_transform: { transform_id: e.target.value } }); });
$("#sw-autotf").addEventListener("click", async () => { S.settings = await api.set_settings({ auto_transform: { enabled: !S.settings.auto_transform.enabled } }); renderTransforms(); });
$("#tf-run").addEventListener("click", async () => {
  const text = $("#tf-in").value.trim(); if (!text) return;
  const out = $("#tf-out"), btn = $("#tf-run");
  btn.disabled = true; btn.textContent = "Running…"; out.className = "out dim"; out.textContent = "Thinking…";
  const r = await api.test_transform($("#tf-try-sel").value, text);
  btn.disabled = false; btn.textContent = "Run transform";
  if (r.error) { out.textContent = r.error; return; }
  out.className = "out"; out.textContent = r.text;
});

/* ---------------- settings ---------------- */
const LANGS = [["en", "English"], ["", "Auto-detect"], ["es", "Spanish"], ["fr", "French"], ["de", "German"], ["it", "Italian"], ["pt", "Portuguese"], ["zh", "Chinese"], ["ja", "Japanese"], ["ko", "Korean"], ["hi", "Hindi"], ["ar", "Arabic"], ["ru", "Russian"], ["nl", "Dutch"], ["vi", "Vietnamese"], ["tl", "Tagalog"]];
async function renderSettings() {
  const s = S.settings;
  $("#hk-show").innerHTML = keysHtml(s.dictate_hotkey);
  $$("#hk-mode button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === s.hotkey_mode));
  $("#tf-mods").innerHTML = keysHtml(s.transform_modifiers, "1–9");
  $("#cmd-show").innerHTML = keysHtml(s.command_hotkey || []);
  $("#set-name").value = s.user_name || "";
  $("#set-lang").innerHTML = LANGS.map(([v, n]) => `<option value="${v}">${n}</option>`).join("");
  $("#set-lang").value = s.language || "";
  $("#set-wmodel").value = s.whisper_model;
  $$("#set-dev button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === s.whisper_device));
  $("#dev-row").hidden = S.platform === "mac";
  $("#set-llm").value = s.llm_model;
  $$("[data-set]").forEach((b) => b.setAttribute("aria-checked", !!s[b.dataset.set]));
  $("#data-dir").textContent = S.dataDir || "";
  $("#sw-login").setAttribute("aria-checked", !!S.launchAtLogin);
  renderEngineSettings();
  const mics = await api.list_mics();
  $("#set-mic").innerHTML = `<option value="">System default</option>` + mics.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("");
  $("#set-mic").value = s.mic_device == null ? "" : String(s.mic_device);
}
function renderEngineSettings() {
  const st = S.stt || {}, ll = S.llm || {};
  $("#stt-pill").className = "pill " + ({ ready: "good", loading: "warn", error: "bad" }[st.state] || "plain");
  $("#stt-pill").textContent = st.state === "ready" ? `${st.backend} · ${st.device}` : st.state || "—";
  $("#stt-err").hidden = !st.error; $("#stt-err").textContent = st.error || "";
  const cloud = ll.provider === "cloud";
  $$("#set-provider button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === (cloud ? "cloud" : "local")));
  $("#key-row").hidden = !cloud;
  $("#set-key").placeholder = ll.has_key ? "•••••••• saved" : "sk-ant-…";
  if (cloud) {
    $("#llm-pill").className = "pill " + (ll.has_key ? "good" : "warn");
    $("#llm-pill").textContent = ll.has_key ? ll.cloud_model : "needs API key";
  } else {
    $("#llm-pill").className = "pill " + (ll.running && ll.installed ? "good" : ll.running ? "warn" : "bad");
    $("#llm-pill").textContent = ll.running ? (ll.installed ? "ready" : "not downloaded") : "Ollama offline";
  }
  $("#llm-list").innerHTML = (ll.models || []).map((m) => `<option value="${esc(m)}">`).join("");
}
$("#set-name").addEventListener("change", async (e) => { S.settings = await api.set_settings({ user_name: e.target.value.trim() }); greet(); });
$("#set-lang").addEventListener("change", async (e) => { S.settings = await api.set_settings({ language: e.target.value }); });
$("#set-mic").addEventListener("change", async (e) => { S.settings = await api.set_settings({ mic_device: e.target.value === "" ? null : +e.target.value }); });
$("#set-wmodel").addEventListener("change", async (e) => { S.settings = await api.set_settings({ whisper_model: e.target.value }); toast("Loading new speech model… first use downloads it", ""); });
$("#set-dev").addEventListener("click", async (e) => { const b = e.target.closest("button"); if (!b) return; S.settings = await api.set_settings({ whisper_device: b.dataset.v }); renderSettings(); });
$("#hk-mode").addEventListener("click", async (e) => { const b = e.target.closest("button"); if (!b) return; S.settings = await api.set_settings({ hotkey_mode: b.dataset.v }); renderSettings(); });
$$("[data-set]").forEach((b) => b.addEventListener("click", async () => { S.settings = await api.set_settings({ [b.dataset.set]: !S.settings[b.dataset.set] }); renderSettings(); }));
$("#set-llm").addEventListener("change", async (e) => { const v = e.target.value.trim(); if (v) S.settings = await api.set_settings({ llm_model: v }); });
$("#llm-pull").addEventListener("click", async () => {
  const name = $("#set-llm").value.trim(); if (!name) return;
  $("#pull-bar").hidden = false; $("#pull-txt").hidden = false; $("#pull-txt").textContent = "Starting download…";
  await api.pull_model(name);
});
$("#set-provider").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.settings = await api.set_settings({ llm_provider: b.dataset.v });
  S.llm = await api.get_state().then((s) => s.llm); renderEngines();
});
$("#key-save").addEventListener("click", async () => {
  const k = $("#set-key").value.trim(); if (!k) return;
  S.llm = await api.save_api_key(k); $("#set-key").value = ""; renderEngines();
  const r = await api.test_cloud();
  toast(r.ok ? `Connected to ${r.model}` : r.error, r.ok ? "good" : "bad");
});
$("#key-test").addEventListener("click", async () => {
  const r = await api.test_cloud();
  toast(r.ok ? `Connected to ${r.model}` : r.error, r.ok ? "good" : "bad");
});
$("#open-data").addEventListener("click", () => api.open_data_dir());
$("#sw-login").addEventListener("click", async () => {
  S.launchAtLogin = await api.set_launch_at_login(!S.launchAtLogin);
  $("#sw-login").setAttribute("aria-checked", !!S.launchAtLogin);
  toast(S.launchAtLogin ? "Hush will open when you log in" : "Hush won't open at log in", "good");
});
$("#quit").addEventListener("click", () => confirmBox("Quit Hush?", "Your dictation shortcut stops working until you open Hush again.", "Quit", () => api.quit()));

// Shortcut capture. The keys are recorded by Hush's global key listener (not this page), so what you press is
// exactly what will trigger it, including keys a web page can't see such as Fn / 🌐 on a Mac.
const CAPTURE_INFO = {
  dictate: ["Dictate shortcut", "Hold the keys together, then let go. A single key works too (Right Alt, Fn on a Mac)."],
  command: ["Command shortcut", "Hold the keys together, then let go. Tip: include your dictate keys plus one more, so you can switch mid-dictation."],
  transform: ["Transform keys", "Press just the modifier keys (like Win + Alt). The slot number 1–9 is added when you use it."],
};
$$("[data-capture]").forEach((btn) => btn.addEventListener("click", () => {
  const which = btn.dataset.capture, [title, help] = CAPTURE_INFO[which];
  modal(`<h3>${title}</h3><p class="note">${help} Esc cancels.</p>
    <div class="out" id="cap" style="min-height:70px;display:grid;place-items:center">Press your new shortcut…</div>
    <p class="note" id="cap-err" style="color:var(--bad)" hidden></p>
    <div class="mact"><button class="btn" data-x>Cancel</button><button class="btn" data-again hidden>Try again</button><button class="btn primary" data-ok disabled>Save</button></div>`, (box, close) => {
    const cap = box.querySelector("#cap"), ok = box.querySelector("[data-ok]"), again = box.querySelector("[data-again]"), err = box.querySelector("#cap-err");
    let combo = [], open = true;
    const listen = async () => {
      err.hidden = true; ok.disabled = true; again.hidden = true;
      cap.textContent = "Press your new shortcut…";
      combo = await api.capture_hotkey();
      if (!open) return;
      if (!combo.length) { cap.textContent = "Nothing captured"; again.hidden = false; return; }
      cap.innerHTML = keysHtml(combo); ok.disabled = false; again.hidden = false;
    };
    box.querySelector("[data-x]").onclick = () => { open = false; close(); };
    again.onclick = listen;
    ok.onclick = async () => {
      const r = await api.set_hotkey(which, combo);
      if (!r.ok) { err.textContent = r.error; err.hidden = false; return; }
      S.settings = r.settings; open = false; close();
      renderSettings(); renderLive(); toast("Shortcut saved", "good");
    };
    listen();
  });
}));

/* ---------------- events from Python ---------------- */
window.flowEvent = (name, data) => {
  if (name === "stt") { S.stt = data; renderEngines(); }
  if (name === "llm") { S.llm = data; renderEngines(); }
  if (name === "level") { S.level = data; }
  if (name === "recording") {
    S.recording = !!data.on; S.recLocked = !!data.locked; S.recApp = data.app || "";
    $("#hero").classList.toggle("rec", S.recording); renderLive();
    $("#hero-eye").textContent = S.recording ? (S.recLocked ? "Hands-free" : "Listening") : "Dictation";
  }
  if (name === "dictation") {
    S.stats = data.stats;
    if (!S.histQ) S.hist.unshift(data.row);
    if ($('.page.show[data-page="home"]')) { renderHomeStats(); renderHistory(data.row.id); }
    if ($('.page.show[data-page="insights"]')) renderInsights();
  }
  if (name === "toast") toast(data.text, data.kind);
  if (name === "deleted") {  // "scratch that" removed the last dictation
    S.stats = data.stats;
    S.hist = S.hist.filter((x) => x.id !== data.id);
    if ($('.page.show[data-page="home"]')) { renderHomeStats(); renderHistory(); }
  }
  if (name === "learned") {
    toast(`Learned “${data.phrase}” from your correction`, "good", {
      label: "Undo",
      run: async () => { S.dict = await api.delete_dictionary(data.id); if ($('.page.show[data-page="dictionary"]')) renderDict(); },
    });
    if ($('.page.show[data-page="dictionary"]')) loadDict();
  }
  if (name === "note_level") {
    // scrolling meter: shift every bar left one step, newest level on the right
    const all = $$("#note-meter i");
    for (let i = 0; i < all.length - 1; i++) all[i].style.height = all[i + 1].style.height || "4px";
    all[all.length - 1].style.height = 4 + Math.min(30, data * 40) + "px";
  }
  if (name === "note_progress") {
    if (S.noteSel === data.id) {
      const bar = $("#np-bar"), txt = $("#np-txt");
      if (bar) bar.style.width = Math.round(data.p * 100) + "%";
      if (txt) txt.textContent = data.stage === "summary" ? "Writing the summary…" : `Transcribing… ${Math.round(data.p * 100)}%`;
    }
  }
  if (name === "note") { loadNotes(); if (S.noteSel === data.id) selectNote(data.id); toast(`Notes ready: ${data.title}`, "good"); }
  if (name === "pull") {
    const bar = $("#pull-bar i"), txt = $("#pull-txt");
    if (data.error) { txt.textContent = "Download failed: " + data.error; return; }
    if (data.p != null) bar.style.width = Math.round(data.p * 100) + "%";
    txt.textContent = data.status + (data.p != null ? ` · ${Math.round(data.p * 100)}%` : "");
    if (data.status === "success") { setTimeout(() => { $("#pull-bar").hidden = true; }, 1500); }
  }
};

/* ---------------- boot ---------------- */
async function boot() {
  const st = await api.get_state();
  Object.assign(S, { settings: st.settings, stats: st.stats, stt: st.stt, llm: st.llm, platform: st.platform, dataDir: st.data_dir, launchAtLogin: st.launch_at_login });
  document.documentElement.dataset.platform = st.platform;
  greet(); renderEngines();
  if (st.note_recording) setNoteUI(true, st.note_started);
  let page = "home"; try { page = localStorage.getItem("flow.page") || "home"; } catch (e) {}
  go(TITLES[page] ? page : "home");
  setInterval(greet, 60000);
  if (!S.settings.welcomed) welcome();
}

/* first run: everything starts blank, so say hello and ask what to call the user */
function welcome() {
  const mac = S.platform === "mac";
  modal(`<h3>Welcome to Hush</h3>
    <p class="note">Talk instead of typing, in any app. Everything runs on this computer unless you choose Claude for cleanup.</p>
    <label class="field">What should Hush call you? <small>Optional, only used for greetings and meeting notes. You can change it in Settings.</small>
      <input class="tin" id="w-name" placeholder="Your first name" autocomplete="off"></label>
    <div class="kv"><span>Dictate</span><b>Hold ${keysHtml(S.settings.dictate_hotkey)} and talk</b></div>
    <div class="kv"><span>Hands-free</span><b>Double-tap it</b></div>
    <div class="kv"><span>Command mode</span><b>Hold ${keysHtml(S.settings.command_hotkey || [])}</b></div>
    ${mac ? `<p class="note">On a Mac, allow Hush under <b>Microphone</b>, <b>Accessibility</b> and <b>Input Monitoring</b> in System Settings → Privacy & Security, and set <b>Keyboard → Press 🌐 key to: Do Nothing</b>.</p>` : ""}
    <div class="mact"><button class="btn" data-skip>Skip</button><button class="btn primary" data-ok>Get started</button></div>`, (box, close) => {
    const done = async (save) => {
      const name = save ? box.querySelector("#w-name").value.trim() : "";
      S.settings = await api.set_settings(name ? { user_name: name, welcomed: true } : { welcomed: true });
      greet(); close();
    };
    box.querySelector("[data-ok]").onclick = () => done(true);
    box.querySelector("[data-skip]").onclick = () => done(false);
    box.querySelector("#w-name").addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); });
  });
}
if (window.pywebview && window.pywebview.api) { api = window.pywebview.api; boot(); }
else window.addEventListener("pywebviewready", () => { api = window.pywebview.api; boot(); });
// Plain-browser preview (no Python): load a mock so the UI can be inspected
setTimeout(() => { if (!api && window.FLOW_MOCK) { api = window.FLOW_MOCK; boot(); } }, 400);
