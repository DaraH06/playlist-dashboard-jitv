let currentBrowsePath = ""; // folder currently shown in the library panel

// Client-side timer smoothing state: we only get fresh elapsed/duration
// numbers from the server every 3s (poll interval), which looks jumpy.
// Instead we remember the last known position + when we learned it, and
// tick the on-screen display every 1s by extrapolating locally. Each new
// poll re-syncs the baseline, so drift never accumulates for more than
// one poll interval.
let timerBaseline = { elapsedSec: null, durationSec: null, atMs: null, paused: true };

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  return res.json();
}

function displayName(relPath) {
  return relPath.split("/").pop();
}

// ---------- Library browser (folders + files) ----------

function renderBreadcrumb(crumbs) {
  const nav = document.getElementById("breadcrumb");
  nav.innerHTML = "";

  const root = document.createElement("a");
  root.textContent = "Semua Video";
  root.href = "#";
  root.onclick = (e) => { e.preventDefault(); browseTo(""); };
  nav.appendChild(root);

  crumbs.forEach((c) => {
    nav.appendChild(document.createTextNode(" / "));
    const a = document.createElement("a");
    a.textContent = c.name;
    a.href = "#";
    a.onclick = (e) => { e.preventDefault(); browseTo(c.path); };
    nav.appendChild(a);
  });
}

function renderBrowseList(data) {
  const ul = document.getElementById("browse-list");
  ul.innerHTML = "";

  if (data.folders.length === 0 && data.files.length === 0) {
    ul.innerHTML = "<li>Folder ini kosong</li>";
    return;
  }

  data.folders.forEach((folder) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "file-name folder-name";
    span.textContent = "📁 " + folder.name;
    const btn = document.createElement("button");
    btn.textContent = "Buka";
    btn.onclick = () => browseTo(folder.path);
    li.append(span, btn);
    ul.appendChild(li);
  });

  data.files.forEach((file) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "file-name";
    span.textContent = file.name;
    li.appendChild(span);
    ul.appendChild(li);
  });
}

async function browseTo(path) {
  currentBrowsePath = path;
  const data = await api("/api/browse?path=" + encodeURIComponent(path));
  if (data.error) {
    alert("Tidak bisa membuka folder ini: " + data.error);
    return;
  }
  renderBreadcrumb(data.crumbs);
  renderBrowseList(data);
}



// ---------- Live player status: played / playing / upcoming ----------

function renderQueueList(elementId, items, emptyLabel) {
  const ul = document.getElementById(elementId);
  ul.innerHTML = "";
  if (items.length === 0) {
    ul.innerHTML = `<li class="queue-empty">${emptyLabel}</li>`;
    return;
  }
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item.duration_str ? `${item.name} (${item.duration_str})` : item.name;
    ul.appendChild(li);
  });
}

function renderNowPlayingTimer(s) {
  const el = document.getElementById("now-playing-timer");
  if (!s.running || s.current_time_pos == null) {
    el.innerHTML = "";
    timerBaseline = { elapsedSec: null, durationSec: null, atMs: null, paused: true };
    return;
  }

  // Re-sync the baseline to the authoritative value from the server.
  timerBaseline = {
    elapsedSec: s.current_time_pos || 0,
    durationSec: s.current_duration_sec || 0,
    atMs: Date.now(),
    paused: !!s.paused,
  };

  if (el.children.length === 0) {
    const bar = document.createElement("div");
    bar.className = "timer-bar";
    bar.innerHTML = '<div class="timer-bar-fill"></div>';
    const label = document.createElement("div");
    label.className = "timer-label";
    el.append(bar, label);
  }

  tickTimerDisplay();
}

function tickTimerDisplay() {
  const el = document.getElementById("now-playing-timer");
  if (!el || timerBaseline.atMs == null) return;

  const fill = el.querySelector(".timer-bar-fill");
  const label = el.querySelector(".timer-label");
  if (!fill || !label) return;

  let elapsedSec = timerBaseline.elapsedSec;
  if (!timerBaseline.paused) {
    elapsedSec += (Date.now() - timerBaseline.atMs) / 1000;
  }
  const totalSec = timerBaseline.durationSec || 0;
  elapsedSec = Math.min(elapsedSec, totalSec || elapsedSec);
  const pct = totalSec > 0 ? Math.min(100, (elapsedSec / totalSec) * 100) : 0;

  fill.style.width = pct + "%";
  label.textContent = `${formatSeconds(elapsedSec)} / ${formatSeconds(totalSec)}`;
}

function formatSeconds(seconds) {
  if (seconds == null) return "--:--";
  seconds = Math.floor(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = h ? String(m).padStart(2, "0") : m;
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// Local 1-second ticker — keeps the bar/label moving smoothly between
// the (slower) 3-second status polls, instead of visibly jumping.
setInterval(tickTimerDisplay, 1000);

async function pollStatus() {
  const s = await api("/api/status");
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");
  const chunkText = document.getElementById("chunk-text");

  if (!s.running) {
    dot.classList.remove("on");
    text.textContent = "Player belum jalan (akan otomatis start saat kamu tekan Play/Terapkan)";
    chunkText.textContent = "";
    renderQueueList("queue-played", [], "Belum ada video yang diputar");
    renderQueueList("queue-playing", [], "Tidak ada yang sedang diputar");
    renderQueueList("queue-upcoming", [], "Belum ada playlist aktif");
    document.getElementById("now-playing-timer").innerHTML = "";
    return;
  }

  dot.classList.add("on");
  const state = s.paused ? "Paused" : "Playing";
  const file = s.current_file ? s.current_file.split("/").pop() : "-";
  text.textContent = `${state} — ${file} (${(s.playlist_index ?? 0) + 1}/${s.playlist_count ?? 0})`;

  if (s.precision_timeline) {
    // Mode Presisi punya counter restart-nya sendiri (switch_count),
    // terpisah dari chunk_progress mode Sederhana yang sengaja
    // berhenti nambah selama Mode Presisi aktif.
    const sisa = s.precision_restart_every - s.precision_switch_count;
    chunkText.textContent = `refresh player otomatis dalam ${sisa} video (Mode Presisi)`;
  } else {
    chunkText.textContent = `refresh player otomatis dalam ${s.chunk_size - s.chunk_progress} video`;
  }

  if (s.precision_timeline) {
    // Mode Presisi: mpv cuma pernah dimuati satu file per saat, jadi
    // daftar sudah-diputar/berikutnya diambil dari jadwal presisi,
    // bukan dari playlist bawaan mpv (yang selalu kosong/habis di sini).
    const t = s.precision_timeline;
    renderQueueList("queue-played", t.played, "Belum ada video yang diputar");
    renderQueueList("queue-playing", t.playing, "Tidak ada yang sedang diputar");
    renderQueueList("queue-upcoming", t.upcoming, "Belum ada jadwal berikutnya hari ini");
  } else {
    const played = s.playlist.filter((it) => it.state === "played");
    const playing = s.playlist.filter((it) => it.state === "playing");
    const upcoming = s.playlist.filter((it) => it.state === "upcoming");

    renderQueueList("queue-played", played, "Belum ada video yang diputar");
    renderQueueList("queue-playing", playing, "Tidak ada yang sedang diputar");
    renderQueueList("queue-upcoming", upcoming, "Playlist sudah habis");
  }
  renderNowPlayingTimer(s);
}

// ---------- Chunk-size setting (auto-restart threshold) ----------

async function loadChunkSizeSetting() {
  const s = await api("/api/settings");
  const input = document.getElementById("chunk-size-input");
  input.min = s.chunk_size_min;
  input.max = s.chunk_size_max;
  input.value = s.chunk_size;
}

document.getElementById("btn-save-chunk-size").onclick = async () => {
  const input = document.getElementById("chunk-size-input");
  const note = document.getElementById("chunk-size-saved-note");
  const value = parseInt(input.value, 10);

  if (isNaN(value) || value < parseInt(input.min, 10) || value > parseInt(input.max, 10)) {
    note.textContent = `Masukkan angka ${input.min}-${input.max}`;
    note.className = "chunk-size-saved-note error";
    return;
  }

  const res = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({ chunk_size: value }),
  });

  if (res.error) {
    note.textContent = res.error;
    note.className = "chunk-size-saved-note error";
    return;
  }

  input.value = res.chunk_size;
  note.textContent = "Tersimpan ✓";
  note.className = "chunk-size-saved-note ok";
  setTimeout(() => { note.textContent = ""; }, 2500);
  pollStatus();
};

// ---------- Named playlists (for scheduling) ----------

async function loadNamedPlaylists() {
  const playlists = await api("/api/named-playlists");
  const names = Object.keys(playlists);

  const ul = document.getElementById("named-playlist-list");
  ul.innerHTML = "";
  if (names.length === 0) {
    ul.innerHTML = "<li>Belum ada playlist bernama tersimpan</li>";
  } else {
    names.forEach((name) => {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.className = "file-name";
      span.textContent = `${name} (${playlists[name].length} video)`;

      const btnGroup = document.createElement("div");
      btnGroup.className = "btn-group";

      const btnRundown = document.createElement("button");
      btnRundown.textContent = "👁 Rundown";
      btnRundown.title = "Lihat rundown siaran tanggal ini";
      btnRundown.onclick = () => openRundown(name);

      const btnHapus = document.createElement("button");
      btnHapus.textContent = "Hapus";
      btnHapus.onclick = async () => {
        await api(`/api/named-playlists/${encodeURIComponent(name)}`, { method: "DELETE" });
        loadNamedPlaylists();
      };

      btnGroup.append(btnRundown, btnHapus);
      li.append(span, btnGroup);
      ul.appendChild(li);
    });
  }
}

// ---------- Rundown Modal ----------

function _secondsToClockWIB(totalSeconds) {
  // Jam tayang di file playlist bisa >= 24 (jadwal melewati tengah malam).
  // Tampilkan apa adanya (tanpa bungkus ke 0) supaya operator tahu jam persisnya.
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = Math.floor(totalSeconds % 60);
  return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}

async function openRundown(dateName) {
  const modal   = document.getElementById("rundown-modal");
  const title   = document.getElementById("rundown-title");
  const summary = document.getElementById("rundown-summary");
  const tbody   = document.getElementById("rundown-tbody");

  title.textContent = `Rundown Siaran: ${dateName}`;
  summary.textContent = "Memuat…";
  tbody.innerHTML = "";
  modal.classList.add("open");

  const res = await fetch(`/api/precision/playlists/${encodeURIComponent(dateName)}`);
  if (!res.ok) {
    summary.textContent = "Gagal memuat data rundown.";
    return;
  }
  const entries = await res.json();

  // Hitung statistik ringkasan
  let countVideo = 0, countMissing = 0, countLive = 0, totalDurSec = 0;
  entries.forEach(e => {
    totalDurSec += e.duration || 0;
    if (e.type === "video")        countVideo++;
    else if (e.type === "missing") countMissing++;
    else if (e.type === "live")    countLive++;
  });

  const totalDurStr = formatSeconds(totalDurSec);
  summary.innerHTML =
    `<span>📊 ${entries.length} program</span>` +
    `<span>⏱ Total siaran: ${totalDurStr}</span>` +
    `<span style="color:#4caf50">🟢 ${countVideo} siap</span>` +
    (countMissing ? `<span style="color:#e05a5a">🔴 ${countMissing} missing</span>` : "") +
    (countLive    ? `<span style="color:#64b5f6">📡 ${countLive} live</span>` : "");

  // Render baris tabel
  entries.forEach(e => {
    const tr = document.createElement("tr");
    if (e.type === "missing") tr.className = "row-missing";
    else if (e.type === "live") tr.className = "row-live";

    let badgeHtml;
    if (e.type === "video")
      badgeHtml = `<span class="badge badge-video">🟢 Siap</span>`;
    else if (e.type === "missing")
      badgeHtml = `<span class="badge badge-missing">🔴 Missing</span>`;
    else
      badgeHtml = `<span class="badge badge-live">📡 Live</span>`;

    // Label: untuk segmen live, potong URL SRT agar tidak terlalu panjang
    const rawLabel = e.label || "-";
    const displayLabel = e.type === "live"
      ? `Relay CCTV (${rawLabel.length > 40 ? rawLabel.slice(0, 40) + "…" : rawLabel})`
      : rawLabel;

    tr.innerHTML =
      `<td class="col-time">${_secondsToClockWIB(e.start)}</td>` +
      `<td class="col-label">${displayLabel}</td>` +
      `<td class="col-dur">${formatSeconds(e.duration)}</td>` +
      `<td class="col-status">${badgeHtml}</td>`;
    tbody.appendChild(tr);
  });
}

function closeRundown() {
  document.getElementById("rundown-modal").classList.remove("open");
}

// Tutup modal: tombol ✕, klik di luar kotak, tombol Escape
document.getElementById("btn-close-rundown").onclick = closeRundown;
document.getElementById("rundown-modal").addEventListener("click", (e) => {
  if (e.target === document.getElementById("rundown-modal")) closeRundown();
});



document.getElementById("btn-import-zip").onclick = async () => {
  const input = document.getElementById("import-zip-input");
  const note = document.getElementById("import-zip-note");

  if (!input.files || input.files.length === 0) {
    note.textContent = "Pilih file .zip / .playlist / .ply / .txt dulu";
    note.className = "chunk-size-saved-note error";
    return;
  }

  const formData = new FormData();
  for (const f of input.files) {
    formData.append("file", f);
  }

  note.textContent = "Memproses file, mohon tunggu...";
  note.className = "chunk-size-saved-note";

  const res = await fetch("/api/import-zip-playlists", { method: "POST", body: formData });
  const data = await res.json();

  if (data.error) {
    note.textContent = data.error;
    note.className = "chunk-size-saved-note error";
    return;
  }

  input.value = "";
  const totalMatched = data.results.reduce((sum, r) => sum + r.matched_count, 0);
  const totalNotFound = data.results.reduce((sum, r) => sum + r.not_found_count, 0);
  const totalLiveSkipped = data.results.reduce((sum, r) => sum + r.skipped_live_count, 0);

  let summary = `${data.results.length} playlist tanggal dibuat (${totalMatched} video total)`;
  if (totalLiveSkipped > 0) summary += `, ${totalLiveSkipped} segmen live CCTV dilewati`;
  if (totalNotFound > 0) summary += `, ${totalNotFound} video TIDAK ketemu`;
  if (data.errors && data.errors.length > 0) summary += ` | Dilewati: ${data.errors.join("; ")}`;

  note.textContent = summary;
  note.className = (totalNotFound > 0 || (data.errors && data.errors.length > 0))
    ? "chunk-size-saved-note error" : "chunk-size-saved-note ok";

  loadNamedPlaylists();
};



// ---------- Wire up controls ----------

document.getElementById("btn-play").onclick = () => api("/api/play", { method: "POST" }).then(pollStatus);
document.getElementById("btn-pause").onclick = () => api("/api/pause", { method: "POST" }).then(pollStatus);
document.getElementById("btn-stop").onclick = () => api("/api/stop", { method: "POST" }).then(pollStatus);
document.getElementById("btn-next").onclick = () => api("/api/next", { method: "POST" }).then(pollStatus);
document.getElementById("btn-prev").onclick = () => api("/api/prev", { method: "POST" }).then(pollStatus);



// ---------- Mode Jadwal Presisi ----------

function formatClock(totalSeconds) {
  const s = Math.floor(totalSeconds % 60);
  const m = Math.floor((totalSeconds / 60) % 60);
  const h = Math.floor(totalSeconds / 3600);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

async function loadPrecisionStatus() {
  const s = await api("/api/precision/status");
  const text = document.getElementById("precision-status-text");
  const detail = document.getElementById("precision-detail");

  if (!s.has_schedule_today) {
    text.textContent = `Aktif, tapi tidak ada jadwal presisi untuk hari ini (${s.today})`;
    detail.innerHTML = "";
    return;
  }

  text.textContent = `Aktif — jadwal ${s.today} (${s.total_entries_today} entri)`;

  let html = "";
  if (s.current) {
    const label = s.current.type === "video" ? s.current.label
      : s.current.type === "missing" ? `${s.current.label} (TIDAK KETEMU — blank)`
      : s.current.type === "live" ? "Segmen live CCTV (blank, belum didukung)"
      : "Blank";
    html += `<div><strong>Sekarang:</strong> ${label} — mulai ${formatClock(s.current.start)}</div>`;
  } else {
    html += `<div><strong>Sekarang:</strong> tidak ada entri aktif (blank)</div>`;
  }
  if (s.upcoming) {
    html += `<div><strong>Berikutnya:</strong> ${s.upcoming.label} — mulai ${formatClock(s.upcoming.start)}</div>`;
  }
  detail.innerHTML = html;
}

browseTo("");
loadChunkSizeSetting();
loadNamedPlaylists();
loadPrecisionStatus();
loadFallbackVideos();
pollStatus();
setInterval(pollStatus, 3000);
setInterval(loadPrecisionStatus, 3000);


// ---------- Fallback Video Manager ----------

let _fallbackList = [];  // current saved list (rel paths)
let _pickerPath = "";    // current browse path inside the picker modal

async function loadFallbackVideos() {
  _fallbackList = await api("/api/fallback-videos");
  renderFallbackList();
}

function renderFallbackList() {
  const ul = document.getElementById("fallback-list");
  ul.innerHTML = "";
  if (_fallbackList.length === 0) {
    ul.innerHTML = "<li class=\"queue-empty\">Belum ada video pengganti. Klik \"+ Tambah\" untuk memilih dari pustaka.</li>";
    return;
  }
  _fallbackList.forEach((rel, idx) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "file-name";
    span.textContent = `📺 ${displayName(rel)}`;
    span.title = rel;

    const btn = document.createElement("button");
    btn.textContent = "Hapus";
    btn.className = "btn-danger";
    btn.onclick = async () => {
      _fallbackList.splice(idx, 1);
      await api("/api/fallback-videos", {
        method: "POST",
        body: JSON.stringify({ items: _fallbackList }),
      });
      renderFallbackList();
    };

    li.append(span, btn);
    ul.appendChild(li);
  });
}

// --- Picker modal ---

async function openFallbackPicker() {
  document.getElementById("fallback-picker-modal").classList.add("open");
  await pickerBrowseTo("");
}

function closeFallbackPicker() {
  document.getElementById("fallback-picker-modal").classList.remove("open");
}

async function pickerBrowseTo(path) {
  _pickerPath = path;
  const data = await api("/api/browse?path=" + encodeURIComponent(path));
  if (data.error) { alert("Error: " + data.error); return; }

  // Breadcrumb
  const nav = document.getElementById("fallback-picker-breadcrumb");
  nav.innerHTML = "";
  const rootA = document.createElement("a");
  rootA.textContent = "Semua Video";
  rootA.href = "#";
  rootA.onclick = (e) => { e.preventDefault(); pickerBrowseTo(""); };
  nav.appendChild(rootA);
  data.crumbs.forEach(c => {
    nav.appendChild(document.createTextNode(" / "));
    const a = document.createElement("a");
    a.textContent = c.name;
    a.href = "#";
    a.onclick = (e) => { e.preventDefault(); pickerBrowseTo(c.path); };
    nav.appendChild(a);
  });

  // List
  const ul = document.getElementById("fallback-picker-list");
  ul.innerHTML = "";
  if (data.folders.length === 0 && data.files.length === 0) {
    ul.innerHTML = "<li>Folder kosong</li>";
    return;
  }
  data.folders.forEach(folder => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "file-name folder-name";
    span.textContent = "📁 " + folder.name;
    const btn = document.createElement("button");
    btn.textContent = "Buka";
    btn.onclick = () => pickerBrowseTo(folder.path);
    li.append(span, btn);
    ul.appendChild(li);
  });
  data.files.forEach(file => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "file-name";
    span.textContent = file.name;

    const btn = document.createElement("button");
    const alreadyAdded = _fallbackList.includes(file.path);
    btn.textContent = alreadyAdded ? "✓ Sudah ditambahkan" : "Pilih";
    btn.disabled = alreadyAdded;
    btn.onclick = async () => {
      if (_fallbackList.includes(file.path)) return;
      _fallbackList.push(file.path);
      const res = await api("/api/fallback-videos", {
        method: "POST",
        body: JSON.stringify({ items: _fallbackList }),
      });
      _fallbackList = res.items;
      renderFallbackList();
      closeFallbackPicker();
    };

    li.append(span, btn);
    ul.appendChild(li);
  });
}

document.getElementById("btn-add-fallback").onclick = openFallbackPicker;
document.getElementById("btn-close-fallback-picker").onclick = closeFallbackPicker;
document.getElementById("fallback-picker-modal").addEventListener("click", (e) => {
  if (e.target === document.getElementById("fallback-picker-modal")) closeFallbackPicker();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeFallbackPicker();
    closeRundown();
  }
});
