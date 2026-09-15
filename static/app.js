let playlist = [];        // list of relative paths (draft, not yet applied)
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
    const btn = document.createElement("button");
    btn.textContent = "+ Tambah";
    btn.onclick = () => {
      if (!playlist.includes(file.path)) {
        playlist.push(file.path);
        renderPlaylist();
      }
    };
    li.append(span, btn);
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

// ---------- Impor playlist dari file .txt ----------

document.getElementById("btn-import-playlist").onclick = async () => {
  const input = document.getElementById("import-file-input");
  const note = document.getElementById("import-note");

  if (!input.files || input.files.length === 0) {
    note.textContent = "Pilih file .txt dulu";
    note.className = "chunk-size-saved-note error";
    return;
  }

  const formData = new FormData();
  formData.append("file", input.files[0]);

  note.textContent = "Memproses...";
  note.className = "chunk-size-saved-note";

  const res = await fetch("/api/import-playlist", { method: "POST", body: formData });
  const data = await res.json();

  if (data.error) {
    note.textContent = data.error;
    note.className = "chunk-size-saved-note error";
    return;
  }

  playlist = data.matched;
  renderPlaylist();
  input.value = "";

  if (data.not_found.length > 0) {
    const preview = data.not_found.slice(0, 5).join(", ");
    const more = data.not_found.length > 5 ? `, dan ${data.not_found.length - 5} lainnya` : "";
    note.textContent = `${data.matched.length} video diimpor. ${data.not_found.length} TIDAK ketemu: ${preview}${more}`;
    note.className = "chunk-size-saved-note error";
  } else {
    note.textContent = `${data.matched.length} video berhasil diimpor ✓`;
    note.className = "chunk-size-saved-note ok";
  }
};

// ---------- Draft playlist editor ----------

function renderPlaylist() {
  const ul = document.getElementById("playlist-list");
  ul.innerHTML = "";
  if (playlist.length === 0) {
    ul.innerHTML = "<li>Playlist kosong — tambahkan dari Pustaka Video</li>";
    return;
  }
  playlist.forEach((relPath, i) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "file-name";
    span.textContent = `${i + 1}. ${displayName(relPath)}`;

    const actions = document.createElement("div");

    const up = document.createElement("button");
    up.textContent = "↑";
    up.onclick = () => {
      if (i > 0) {
        [playlist[i - 1], playlist[i]] = [playlist[i], playlist[i - 1]];
        renderPlaylist();
      }
    };

    const down = document.createElement("button");
    down.textContent = "↓";
    down.onclick = () => {
      if (i < playlist.length - 1) {
        [playlist[i + 1], playlist[i]] = [playlist[i], playlist[i + 1]];
        renderPlaylist();
      }
    };

    const remove = document.createElement("button");
    remove.textContent = "✕";
    remove.onclick = () => {
      playlist.splice(i, 1);
      renderPlaylist();
    };

    actions.append(up, down, remove);
    li.append(span, actions);
    ul.appendChild(li);
  });
}

async function loadPlaylist() {
  playlist = await api("/api/playlist");
  renderPlaylist();
}

async function savePlaylist() {
  await api("/api/playlist", {
    method: "POST",
    body: JSON.stringify({ items: playlist }),
  });
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
      const btn = document.createElement("button");
      btn.textContent = "Hapus";
      btn.onclick = async () => {
        await api(`/api/named-playlists/${encodeURIComponent(name)}`, { method: "DELETE" });
        loadNamedPlaylists();
        loadSchedule();
      };
      li.append(span, btn);
      ul.appendChild(li);
    });
  }

  const select = document.getElementById("schedule-playlist-select");
  const prevValue = select.value;
  select.innerHTML = "";
  if (names.length === 0) {
    select.innerHTML = "<option value=''>Belum ada playlist bernama</option>";
  } else {
    names.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    });
    if (names.includes(prevValue)) select.value = prevValue;
  }
}

document.getElementById("btn-save-named").onclick = async () => {
  const nameInput = document.getElementById("named-playlist-name-input");
  const note = document.getElementById("named-save-note");
  const name = nameInput.value.trim();

  if (!name) {
    note.textContent = "Isi nama playlist dulu";
    note.className = "chunk-size-saved-note error";
    return;
  }
  if (playlist.length === 0) {
    note.textContent = "Playlist Baru masih kosong";
    note.className = "chunk-size-saved-note error";
    return;
  }

  const res = await api("/api/named-playlists", {
    method: "POST",
    body: JSON.stringify({ name, items: playlist }),
  });
  if (res.error) {
    note.textContent = res.error;
    note.className = "chunk-size-saved-note error";
    return;
  }
  note.textContent = `Tersimpan sebagai "${res.name}" ✓`;
  note.className = "chunk-size-saved-note ok";
  nameInput.value = "";
  loadNamedPlaylists();
};

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

// ---------- Schedule ----------

const WEEKDAY_LABELS = { mon: "Sen", tue: "Sel", wed: "Rab", thu: "Kam", fri: "Jum", sat: "Sab", sun: "Min" };

function describeRecurrence(entry) {
  if (entry.recurrence === "daily") return "Setiap hari";
  if (entry.recurrence === "days") {
    return (entry.days || []).map((d) => WEEKDAY_LABELS[d] || d).join(", ");
  }
  if (entry.recurrence === "date") return entry.date;
  return entry.recurrence;
}

async function loadSchedule() {
  const data = await api("/api/schedule");
  const ul = document.getElementById("schedule-list");
  ul.innerHTML = "";

  if (!data.schedule || data.schedule.length === 0) {
    ul.innerHTML = "<li>Belum ada jadwal ditambahkan</li>";
  } else {
    data.schedule.forEach((entry) => {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.className = "file-name";
      span.textContent = `${entry.time} — ${describeRecurrence(entry)} — ${entry.playlist_name}`;
      const btn = document.createElement("button");
      btn.textContent = "Hapus";
      btn.onclick = async () => {
        await api(`/api/schedule/${entry.id}`, { method: "DELETE" });
        loadSchedule();
      };
      li.append(span, btn);
      ul.appendChild(li);
    });
  }

  const lastText = document.getElementById("last-applied-text");
  if (data.last_applied) {
    lastText.textContent = `Terakhir diterapkan otomatis: "${data.last_applied.playlist_name}" pada ${data.last_applied.at}`;
  } else {
    lastText.textContent = "Belum ada yang diterapkan otomatis.";
  }
}

document.getElementById("schedule-recurrence-select").onchange = (e) => {
  document.getElementById("schedule-days-row").style.display = e.target.value === "days" ? "flex" : "none";
  document.getElementById("schedule-date-row").style.display = e.target.value === "date" ? "block" : "none";
};

document.getElementById("btn-add-schedule").onclick = async () => {
  const note = document.getElementById("schedule-add-note");
  const time = document.getElementById("schedule-time-input").value;
  const recurrence = document.getElementById("schedule-recurrence-select").value;
  const playlistName = document.getElementById("schedule-playlist-select").value;

  if (!playlistName) {
    note.textContent = "Pilih playlist dulu (simpan sebagai playlist bernama di atas)";
    note.className = "chunk-size-saved-note error";
    return;
  }
  if (!time) {
    note.textContent = "Isi jam mulai";
    note.className = "chunk-size-saved-note error";
    return;
  }

  const body = { time, playlist_name: playlistName, recurrence };
  if (recurrence === "days") {
    body.days = Array.from(document.querySelectorAll("#schedule-days-row input:checked")).map((el) => el.value);
  } else if (recurrence === "date") {
    body.date = document.getElementById("schedule-date-input").value;
  }

  const res = await api("/api/schedule", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (res.error) {
    note.textContent = res.error;
    note.className = "chunk-size-saved-note error";
    return;
  }
  note.textContent = "Jadwal ditambahkan ✓";
  note.className = "chunk-size-saved-note ok";
  loadSchedule();
};

// ---------- Wire up controls ----------

document.getElementById("btn-play").onclick = () => api("/api/play", { method: "POST" }).then(pollStatus);
document.getElementById("btn-pause").onclick = () => api("/api/pause", { method: "POST" }).then(pollStatus);
document.getElementById("btn-stop").onclick = () => api("/api/stop", { method: "POST" }).then(pollStatus);
document.getElementById("btn-next").onclick = () => api("/api/next", { method: "POST" }).then(pollStatus);
document.getElementById("btn-prev").onclick = () => api("/api/prev", { method: "POST" }).then(pollStatus);

document.getElementById("btn-save").onclick = async () => {
  await savePlaylist();
  alert("Playlist disimpan.");
};

document.getElementById("btn-apply").onclick = async () => {
  await savePlaylist();
  await api("/api/apply", { method: "POST" });
  pollStatus();
};

// ---------- Mode Jadwal Presisi ----------

function formatClock(totalSeconds) {
  const s = Math.floor(totalSeconds % 60);
  const m = Math.floor((totalSeconds / 60) % 60);
  const h = Math.floor(totalSeconds / 3600);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

async function loadPrecisionStatus() {
  const s = await api("/api/precision/status");
  const dot = document.getElementById("precision-dot");
  const text = document.getElementById("precision-status-text");
  const btn = document.getElementById("btn-toggle-precision");
  const detail = document.getElementById("precision-detail");

  dot.classList.toggle("on", s.enabled);
  btn.textContent = s.enabled ? "Matikan Mode Presisi" : "Aktifkan Mode Presisi";

  if (!s.enabled) {
    text.textContent = "Mode presisi tidak aktif (pakai kontrol manual di atas)";
    detail.innerHTML = "";
    return;
  }

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

document.getElementById("btn-toggle-precision").onclick = async () => {
  const s = await api("/api/precision/status");
  const endpoint = s.enabled ? "/api/precision/disable" : "/api/precision/enable";
  await api(endpoint, { method: "POST" });
  loadPrecisionStatus();
};

browseTo("");
loadPlaylist();
loadChunkSizeSetting();
loadNamedPlaylists();
loadSchedule();
loadPrecisionStatus();
pollStatus();
setInterval(pollStatus, 3000);
setInterval(loadSchedule, 15000);
setInterval(loadPrecisionStatus, 3000);
