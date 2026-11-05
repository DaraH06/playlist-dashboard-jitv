let currentBrowsePath = "";
const browseCache = new Map();
const browseErrors = new Map();
const expandedFolders = new Set();

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

// ---------- Pustaka Video Utama (Folder Tree Figma-style) ----------

async function loadBrowseFolder(path) {
  if (browseCache.has(path)) return browseCache.get(path);
  const data = await api("/api/browse?path=" + encodeURIComponent(path));
  if (data.error) throw new Error(data.error);
  browseCache.set(path, data);
  return data;
}

function renderLibraryTree() {
  const tree = document.getElementById("browse-list");
  tree.innerHTML = "";
  const rootData = browseCache.get("");
  if (browseErrors.has("")) {
    tree.appendChild(makeTreeMessage(`Tidak dapat membaca pustaka: ${browseErrors.get("")}`));
  } else if (!rootData) {
    tree.appendChild(makeTreeMessage("Memuat pustaka…"));
  } else {
    appendFolderContents(tree, rootData);
    if (!rootData.folders.length && !rootData.files.length) {
      tree.appendChild(makeTreeMessage("Pustaka kosong"));
    }
  }
}

function renderFolderNode(folder) {
  const li = document.createElement("li");
  li.className = "tree-node";
  const row = document.createElement("div");
  row.className = `tree-row${currentBrowsePath === folder.path ? " selected" : ""}`;
  row.setAttribute("role", "button");
  row.tabIndex = 0;
  row.setAttribute("aria-label", `${expandedFolders.has(folder.path) ? "Collapse" : "Expand"} ${folder.name}`);
  row.onclick = () => toggleFolder(folder.path);
  row.onkeydown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggleFolder(folder.path);
    }
  };
  const isExpanded = expandedFolders.has(folder.path);
  row.append(
    makeTreeToggle(folder.path, isExpanded),
    makeTreeIcon("folder", "/static/assets/folder.svg", "Folder"),
    makeTreeLabel(folder.path, folder.name)
  );
  li.appendChild(row);
  if (isExpanded) {
    const children = document.createElement("ul");
    children.className = "tree-children";
    const data = browseCache.get(folder.path);
    if (browseErrors.has(folder.path)) children.appendChild(makeTreeMessage(`Error: ${browseErrors.get(folder.path)}`));
    else if (!data) children.appendChild(makeTreeMessage("Memuat folder…"));
    else if (!data.folders.length && !data.files.length) children.appendChild(makeTreeMessage("(Folder kosong)"));
    else appendFolderContents(children, data);
    li.appendChild(children);
  }
  return li;
}

function appendFolderContents(list, data) {
  const entries = [
    ...data.folders.map(folder => ({ ...folder, kind: "folder" })),
    ...data.files.map(file => ({ ...file, kind: "video" })),
  ].sort((a, b) => Number(b.kind === "folder") - Number(a.kind === "folder")
    || a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));

  entries.forEach(entry => {
    if (entry.kind === "folder") {
      list.appendChild(renderFolderNode(entry));
      return;
    }
    const li = document.createElement("li");
    li.className = "tree-node";
    const row = document.createElement("div");
    row.className = `tree-row tree-file-row${currentBrowsePath === entry.path ? " selected" : ""}`;
    row.append(
      makeTreeSpacer(),
      makeTreeIcon("video", "/static/assets/video.svg", "Video"),
      makeTreeLabel(entry.path, entry.name)
    );
    if (document.body.classList.contains("create-body")) {
      row.setAttribute("role", "button");
      row.tabIndex = 0;
      row.ondblclick = () => addVideoToSchedule(entry);
      row.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          addVideoToSchedule(entry);
        }
      };
      row.draggable = true;
      row.dataset.videoPath = entry.path;
      row.dataset.videoName = entry.name;
      row.dataset.videoDuration = entry.duration || 0;
      row.addEventListener("dragstart", handleDragStart);
      row.addEventListener("dragend", handleDragEnd);
    }
    li.appendChild(row);
    list.appendChild(li);
  });
}

function makeTreeToggle(path, isExpanded) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tree-toggle";
  const icon = document.createElement("img");
  icon.src = isExpanded ? "/static/assets/minus.svg" : "/static/assets/plus.svg";
  icon.alt = "";
  button.appendChild(icon);
  button.setAttribute("aria-label", `${isExpanded ? "Collapse" : "Expand"} ${path || "Video Library"}`);
  button.setAttribute("aria-expanded", String(isExpanded));
  button.onclick = (event) => { event.stopPropagation(); toggleFolder(path); };
  return button;
}

function makeTreeSpacer() {
  const spacer = document.createElement("span");
  spacer.className = "tree-spacer";
  spacer.setAttribute("aria-hidden", "true");
  return spacer;
}

function makeTreeIcon(kind, src, alt) {
  const wrapper = document.createElement("span");
  wrapper.className = `tree-icon tree-icon-${kind}`;
  const icon = document.createElement("img");
  icon.src = src;
  icon.alt = alt;
  wrapper.appendChild(icon);
  return wrapper;
}

function makeTreeLabel(path, label) {
  const text = document.createElement("span");
  text.className = "tree-label";
  text.textContent = label;
  return text;
}

function makeTreeMessage(message) {
  const li = document.createElement("li");
  li.className = "tree-message";
  li.textContent = message;
  return li;
}

async function toggleFolder(path) {
  currentBrowsePath = path;
  if (expandedFolders.has(path)) {
    expandedFolders.delete(path);
    renderLibraryTree();
    return;
  }
  expandedFolders.add(path);
  renderLibraryTree();
  try {
    await loadBrowseFolder(path);
    browseErrors.delete(path);
    renderLibraryTree();
  } catch (error) {
    browseErrors.set(path, error.message);
    renderLibraryTree();
  }
}

async function browseTo(path) {
  try {
    await loadBrowseFolder(path);
    browseErrors.delete(path);
    if (path) expandedFolders.add(path);
    currentBrowsePath = path;
    renderLibraryTree();
  } catch (error) {
    browseErrors.set(path, error.message);
    renderLibraryTree();
  }
}

// ---------- Status Player: Sudah Diputar / Sedang Diputar / Berikutnya ----------

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

setInterval(tickTimerDisplay, 1000);

async function pollStatus() {
  const s = await api("/api/status");
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");

  if (!s.running) {
    dot.classList.remove("on");
    text.textContent = "Player belum jalan (akan otomatis start saat kamu tekan Play/Terapkan)";
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

// ---------- Playlist Bernama (untuk Penjadwalan) ----------

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

// ---------- Modal Rundown ----------

function _secondsToClockWIB(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = Math.floor(totalSeconds % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

async function openRundown(dateName) {
  const modal = document.getElementById("rundown-modal");
  const title = document.getElementById("rundown-title");
  const summary = document.getElementById("rundown-summary");
  const tbody = document.getElementById("rundown-tbody");

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

  let countVideo = 0, countMissing = 0, countLive = 0, totalDurSec = 0;
  entries.forEach(e => {
    totalDurSec += e.duration || 0;
    if (e.type === "video") countVideo++;
    else if (e.type === "missing") countMissing++;
    else if (e.type === "live") countLive++;
  });

  const totalDurStr = formatSeconds(totalDurSec);
  summary.innerHTML =
    `<span>📊 ${entries.length} program</span>` +
    `<span>⏱ Total siaran: ${totalDurStr}</span>` +
    `<span style="color:#4caf50">🟢 ${countVideo} siap</span>` +
    (countMissing ? `<span style="color:#e05a5a">🔴 ${countMissing} missing</span>` : "") +
    (countLive ? `<span style="color:#64b5f6">📡 ${countLive} live</span>` : "");

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

if (!document.body.classList.contains("create-body")) {
  document.getElementById("btn-close-rundown").onclick = closeRundown;
  document.getElementById("rundown-modal").addEventListener("click", (e) => {
    if (e.target === document.getElementById("rundown-modal")) closeRundown();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeRundown();
  });


  // Import playlist handling

  const input = document.getElementById("import-zip-input");
  input.addEventListener("change", async () => {
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
  });

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
  loadNamedPlaylists();
  loadPrecisionStatus();
  loadFallbackVideos();
  pollStatus();
  setInterval(pollStatus, 3000);
  setInterval(loadPrecisionStatus, 3000);


  // ---------- Manajer Video Fallback ----------

  let _fallbackList = [];  // daftar tersimpan (path relatif)
  let _pickerPath = "";    // path browse saat ini di modal picker

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
    }
  });
}

// ============================================================
// CREATE PLAYLIST
// ============================================================

let scheduleItems = [];
let draggedItem = null;

function initCreatePage() {
  if (!document.body.classList.contains('create-body')) return;
  browseTo("");
  setupEventListeners();
  updateScheduleUI();
  updateFooter();
}

function setupEventListeners() {
  // Back button
  document.getElementById('btn-back').addEventListener('click', () => {
    if (scheduleItems.length > 0) {
      if (confirm('Perubahan tidak tersimpan. Yakin ingin keluar?')) {
        window.location.href = '/';
      }
    } else {
      window.location.href = '/';
    }
  });

  // Clear button
  document.getElementById('btn-clear').addEventListener('click', () => {
    if (scheduleItems.length > 0 && confirm('Hapus semua program dari jadwal?')) {
      scheduleItems = [];
      updateScheduleUI();
      updateFooter();
      showToast('Jadwal dikosongkan', 'info');
    }
  });

  // Start time
  document.getElementById('start-time-input').addEventListener('change', () => {
    recalculateTimes();
    updateScheduleUI();
  });

  // Date input
  document.getElementById('schedule-date').addEventListener('change', () => {
    updateFooter();
  });

  document.getElementById('btn-open-date-picker').addEventListener('click', () => {
    const dateInput = document.getElementById('schedule-date');
    if (typeof dateInput.showPicker === 'function') dateInput.showPicker();
    else {
      dateInput.focus();
      dateInput.click();
    }
  });

  // Save button
  document.getElementById('btn-save').addEventListener('click', openSaveModal);

  document.getElementById('btn-close-save-modal').addEventListener('click', closeSaveModal);
  document.getElementById('btn-cancel-save').addEventListener('click', closeSaveModal);
  document.getElementById('save-modal').addEventListener('click', (e) => {
    if (e.target === document.getElementById('save-modal')) closeSaveModal();
  });

  // Confirm save
  document.getElementById('btn-confirm-save').addEventListener('click', savePlaylist);

  document.getElementById('playlist-name').addEventListener('input', updateSavePreview);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeSaveModal();
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      if (scheduleItems.length > 0) openSaveModal();
    }
  });
}

function addVideoToSchedule(video) {
  if (scheduleItems.some(item => item.path === video.path)) {
    showToast('Video sudah ada di jadwal', 'warning');
    return;
  }

  const startTime = getNextStartTime();
  const duration = video.duration || 0;

  const item = {
    id: Date.now() + Math.random(),
    path: video.path,
    name: video.name,
    duration: duration,
    type: 'video',
    startTime: startTime
  };

  scheduleItems.push(item);
  recalculateTimes();
  updateScheduleUI();
  updateFooter();
  showToast(`Ditambahkan: ${video.name}`, 'success');
}

function getNextStartTime() {
  if (scheduleItems.length === 0) {
    const startInput = document.getElementById('start-time-input');
    return startInput.value || '08:00:00';
  }
  const lastItem = scheduleItems[scheduleItems.length - 1];
  return addSecondsToTime(lastItem.startTime, lastItem.duration);
}

function addSecondsToTime(timeStr, seconds) {
  const [h, m, s] = timeStr.split(':').map(Number);
  let total = h * 3600 + m * 60 + s + seconds;
  const nh = Math.floor(total / 3600) % 24;
  const nm = Math.floor((total % 3600) / 60);
  const ns = total % 60;
  return `${String(nh).padStart(2, '0')}:${String(nm).padStart(2, '0')}:${String(ns).padStart(2, '0')}`;
}

function recalculateTimes() {
  const startInput = document.getElementById('start-time-input');
  let currentTime = startInput.value || '08:00:00';

  scheduleItems.forEach(item => {
    item.startTime = currentTime;
    currentTime = addSecondsToTime(currentTime, item.duration);
  });
}

function removeScheduleItem(id) {
  scheduleItems = scheduleItems.filter(item => item.id !== id);
  recalculateTimes();
  updateScheduleUI();
  updateFooter();
  showToast('Program dihapus', 'info');
}

function moveScheduleItem(fromIndex, toIndex) {
  const [moved] = scheduleItems.splice(fromIndex, 1);
  scheduleItems.splice(toIndex, 0, moved);
  recalculateTimes();
  updateScheduleUI();
}

function updateScheduleUI() {
  const tbody = document.getElementById('schedule-body');
  const clearBtn = document.getElementById('btn-clear');
  const saveBtn = document.getElementById('btn-save');
  const addBreakBtn = document.getElementById('btn-add-break');
  const addLiveBtn = document.getElementById('btn-add-live');

  clearBtn.disabled = scheduleItems.length === 0;
  saveBtn.disabled = scheduleItems.length === 0;
  addBreakBtn.disabled = scheduleItems.length === 0;
  addLiveBtn.disabled = scheduleItems.length === 0;
  updateScheduleSummary();

  if (scheduleItems.length === 0) {
    tbody.innerHTML = `
      <tr class="empty-row">
        <td colspan="5" class="empty-message">
          <span class="empty-video-icon" aria-hidden="true"><img src="/static/assets/video.svg" alt="" class="empty-icon"></span>
          <div class="empty-copy">
            <span>Belum ada video</span>
            <p>Tarik &amp; drop video dari folder<br>atau klik video 2x untuk menambahkan</p>
          </div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = '';
  scheduleItems.forEach((item, index) => {
    const tr = document.createElement('tr');
    tr.draggable = true;
    tr.dataset.index = index;

    // Drag & drop
    tr.addEventListener('dragstart', handleScheduleDragStart);
    tr.addEventListener('dragend', handleScheduleDragEnd);
    tr.addEventListener('dragover', handleScheduleDragOver);
    tr.addEventListener('drop', handleScheduleDrop);
    tr.addEventListener('dragleave', handleScheduleDragLeave);

    const typeBadges = {
      video: '<span class="program-type-badge video">Video</span>',
      break: '<span class="program-type-badge break">Istirahat</span>',
      live: '<span class="program-type-badge live">Live CCTV</span>'
    };

    tr.innerHTML = `
      <td class="col-time">${item.startTime}</td>
      <td class="col-program">
        <div class="program-name">
          <span class="program-order" aria-label="Urutan program">
            <button class="order-arrow" type="button" aria-label="Pindahkan ke atas" title="Pindahkan ke atas" ${index === 0 ? 'disabled' : `onclick="moveScheduleItem(${index}, ${index - 1})"`}>
              <img src="/static/assets/arrow-up.svg" alt="">
            </button>
            <button class="order-arrow" type="button" aria-label="Pindahkan ke bawah" title="Pindahkan ke bawah" ${index === scheduleItems.length - 1 ? 'disabled' : `onclick="moveScheduleItem(${index}, ${index + 1})"`}>
              <img src="/static/assets/arrow-down.svg" alt="">
            </button>
          </span>
          <span class="program-title${item.missing ? ' missing' : ''}">${escapeHtml(item.name)}</span>
          ${typeBadges[item.type] || ''}
        </div>
      </td>
      <td class="col-duration">${formatDuration(item.duration)}</td>
      <td class="col-type">${getTypeLabel(item.type)}</td>
      <td class="col-actions">
        <button class="btn-icon delete" aria-label="Hapus program" title="Hapus" onclick="removeScheduleItem(${item.id})">
          <img src="/static/assets/delete.svg" alt="">
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function updateScheduleSummary() {
  const count = scheduleItems.length;
  const totalSeconds = scheduleItems.reduce((sum, item) => sum + (item.duration || 0), 0);

  document.getElementById('program-count').textContent = `${count} Program`;
  document.getElementById('total-duration').textContent = `Total Siaran: ${formatDuration(totalSeconds)}`;
}

function updateFooter() {
  const count = scheduleItems.length;
  const totalSeconds = scheduleItems.reduce((sum, item) => sum + (item.duration || 0), 0);
  const dateInput = document.getElementById('schedule-date');
  const dateStr = dateInput.value || new Date().toISOString().split('T')[0];

  document.getElementById('footer-count').innerHTML = `<strong>${count}</strong> program`;
  document.getElementById('footer-duration').innerHTML = `<strong>${formatDuration(totalSeconds)}</strong> total`;
}

function getTypeLabel(type) {
  const labels = { video: 'Video', break: 'Istirahat', live: 'Live' };
  return labels[type] || type;
}

function formatDuration(seconds) {
  if (!seconds || seconds < 0) return '00:00:00';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Drag & drop (jadwal)
draggedItem = null;

function handleScheduleDragStart(e) {
  draggedItem = e.target.closest('tr');
  if (!draggedItem) return;
  draggedItem.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', draggedItem.dataset.index);
}

function handleScheduleDragEnd(e) {
  if (draggedItem) {
    draggedItem.classList.remove('dragging');
    document.querySelectorAll('#schedule-body tr').forEach(tr => {
      tr.classList.remove('drag-over');
    });
    draggedItem = null;
  }
}

function handleScheduleDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const target = e.target.closest('tr');
  if (target && target !== draggedItem) {
    document.querySelectorAll('#schedule-body tr').forEach(tr => tr.classList.remove('drag-over'));
    target.classList.add('drag-over');
  }
}

function handleScheduleDrop(e) {
  e.preventDefault();
  const target = e.target.closest('tr');
  if (!target || !draggedItem || target === draggedItem) return;

  const fromIndex = parseInt(draggedItem.dataset.index, 10);
  const toIndex = parseInt(target.dataset.index, 10);

  moveScheduleItem(fromIndex, toIndex);
}

function handleScheduleDragLeave(e) {
  const target = e.target.closest('tr');
  if (target && !target.contains(e.relatedTarget)) {
    target.classList.remove('drag-over');
  }
}

// Drag dari library
draggedLibraryItem = null;

function handleDragStart(e) {
  draggedLibraryItem = e.target.closest('.tree-file-row');
  if (!draggedLibraryItem) return;
  e.dataTransfer.effectAllowed = 'copy';
  e.dataTransfer.setData('text/plain', JSON.stringify({
    path: draggedLibraryItem.dataset.videoPath,
    name: draggedLibraryItem.dataset.videoName,
    duration: parseInt(draggedLibraryItem.dataset.videoDuration, 10) || 0
  }));
  draggedLibraryItem.style.opacity = '0.5';
}

function handleDragEnd(e) {
  if (draggedLibraryItem) {
    draggedLibraryItem.style.opacity = '';
    draggedLibraryItem = null;
  }
}

// Drop zone
const scheduleWrapper = document.getElementById('schedule-table-wrapper');
scheduleWrapper.addEventListener('dragover', (e) => {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'copy';
  scheduleWrapper.classList.add('drag-over');
});

scheduleWrapper.addEventListener('dragleave', (e) => {
  if (!scheduleWrapper.contains(e.relatedTarget)) {
    scheduleWrapper.classList.remove('drag-over');
  }
});

scheduleWrapper.addEventListener('drop', (e) => {
  e.preventDefault();
  scheduleWrapper.classList.remove('drag-over');

  try {
    const data = JSON.parse(e.dataTransfer.getData('text/plain'));
    if (data && data.path) {
      addVideoToSchedule(data);
    }
  } catch (err) { }
});

// Save Modal
function openSaveModal() {
  if (scheduleItems.length === 0) return;

  const modal = document.getElementById('save-modal');
  const nameInput = document.getElementById('playlist-name');
  const descInput = document.getElementById('playlist-description');

  // Default name: today's date
  const dateInput = document.getElementById('schedule-date');
  const defaultName = dateInput.value || new Date().toISOString().split('T')[0];
  nameInput.value = defaultName;
  descInput.value = '';

  updateSavePreview();
  modal.classList.add('open');
  nameInput.focus();
  nameInput.select();
}

function closeSaveModal() {
  document.getElementById('save-modal').classList.remove('open');
}

function updateSavePreview() {
  const nameInput = document.getElementById('playlist-name');
  const preview = document.getElementById('preview-path');
  const name = nameInput.value.trim();
  if (name) {
    preview.textContent = `/playlists/${name}.playlist`;
  } else {
    preview.textContent = `/playlists/[nama].playlist`;
  }
}

async function savePlaylist() {
  const nameInput = document.getElementById('playlist-name');
  const descInput = document.getElementById('playlist-description');
  const name = nameInput.value.trim();
  const description = descInput.value.trim();

  if (!name) {
    showToast('Nama playlist wajib diisi', 'error');
    nameInput.focus();
    return;
  }

  // Validasi
  if (!/^\d{4}-\d{2}-\d{2}$/.test(name)) {
    showToast('Format nama harus yyyy-mm-dd', 'error');
    nameInput.focus();
    return;
  }

  const btn = document.getElementById('btn-confirm-save');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Menyimpan...';

  try {
    const playlistData = {
      name: name,
      description: description,
      items: scheduleItems.map(item => ({
        path: item.path,
        name: item.name,
        duration: item.duration,
        type: item.type,
        start_time: item.startTime
      }))
    };

    const res = await api('/api/named-playlists', {
      method: 'POST',
      body: JSON.stringify(playlistData)
    });

    if (res.error) throw new Error(res.error);

    showToast(`Playlist "${name}" berhasil disimpan`, 'success');
    closeSaveModal();

    setTimeout(() => {
      window.location.href = '/';
    }, 1500);
  } catch (error) {
    showToast('Gagal menyimpan: ' + error.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<img src="/static/assets/save.svg" alt="" class="icon"><span>Simpan Playlist</span>';
  }
}

// Notifikasi
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  const icons = {
    success: '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>',
    error: '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>',
    warning: '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>',
    info: '<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>'
  };

  toast.innerHTML = `
    ${icons[type] || icons.info}
    <span class="toast-message">${escapeHtml(message)}</span>
    <button class="toast-close" aria-label="Tutup">&times;</button>
  `;

  toast.querySelector('.toast-close').addEventListener('click', () => {
    toast.remove();
  });

  container.appendChild(toast);

  setTimeout(() => {
    if (toast.parentNode) {
      toast.style.animation = 'toastIn 0.3s ease-out reverse';
      setTimeout(() => toast.remove(), 300);
    }
  }, 4000);
}

window.removeScheduleItem = removeScheduleItem;
window.addVideoToSchedule = addVideoToSchedule;
window.showToast = showToast;
window.formatDuration = formatDuration;
window.escapeHtml = escapeHtml;
window.initCreatePage = initCreatePage;
window.openSaveModal = openSaveModal;
window.closeSaveModal = closeSaveModal;
window.savePlaylist = savePlaylist;
window.updateSavePreview = updateSavePreview;

// Init DOM (backup)
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCreatePage);
} else {
  initCreatePage();
}
