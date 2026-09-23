"""
Precision (timecode-accurate) playback engine — separate mode from the
simple "apply a playlist, loop through it" mode.

Given a parsed .playlist schedule (list of timed entries for one
calendar date), this engine keeps mpv's on-screen output in sync with
the ACTUAL wall-clock time (WIB), continuously:

  - Figures out which entry SHOULD be playing right now, based on its
    start time + duration.
  - If we just turned this mode on mid-day (e.g. system started at
    11:00 but the schedule's entry started at 10:58), it seeks into
    that video to the correct position instead of starting from 0:00.
  - Switches to the next entry exactly when its start time arrives.
  - Shows a plain black screen (mpv "stop") for: missing files, live
    segments (not supported yet), gaps with nothing scheduled, or when
    no precision playlist exists for today at all.

This is intentionally a from-scratch engine, separate from the mpv
memory-leak mitigation used in simple mode (chunk-restart tied to
mpv's own playlist-pos) — precision mode never uses mpv's playlist
feature, so that mechanism can't observe anything here. Instead this
engine restarts mpv itself every PRECISION_RESTART_EVERY file-switches,
which is safe because the engine always re-derives the correct file +
seek position from the wall clock on its very next tick — no separate
"resume" bookkeeping needed.
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta

# Jam dipatok eksplisit ke WIB (UTC+7), TIDAK ikut timezone sistem.
try:
    from zoneinfo import ZoneInfo
    WIB = ZoneInfo("Asia/Jakarta")
except Exception:
    WIB = timezone(timedelta(hours=7))

# Jam mulai siaran default. Dipakai hanya jika tidak ada playlist aktif
# untuk auto-detect, dan tidak ada override di settings.json.
# Nilai aman: 6 (mencakup siaran yg mulai 06:00–08:00 WIB).
_BROADCAST_START_HOUR_DEFAULT = 6

PRECISION_FILE = os.path.join(os.path.dirname(__file__), "precision_playlists.json")
TICK_SECONDS = 2
PRECISION_RESTART_EVERY = 20  # same rationale/value as simple-mode chunk_size default

TIMECODE_RE = re.compile(r"^(\d+):(\d{2}):(\d{2}):(\d{2})$")


def timecode_to_seconds(tc, fps=25):
    """'HH:MM:SS:FF' -> total seconds (float). Hours can exceed 24 —
    that's how this broadcast format represents times after midnight
    that still belong to the same schedule day."""
    m = TIMECODE_RE.match(tc.strip())
    if not m:
        return None
    h, mnt, s, f = (int(x) for x in m.groups())
    return h * 3600 + mnt * 60 + s + (f / fps)


def parse_playlist_text(text, video_root, safe_path_fn, filename_index):
    """Parse the raw .playlist file content into a time-ordered list of
    entries: {start, duration, end, type, path (if video), label}.
    type is one of: "video", "missing", "live", "marker"."""
    entries = []
    for line in text.splitlines():
        line = line.strip("\r\n")
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) < 3:
            continue

        start = timecode_to_seconds(fields[0])
        duration = timecode_to_seconds(fields[1])
        if start is None or duration is None:
            continue

        content = fields[2].strip()
        if not content or duration <= 0:
            continue  # zero-length marker lines carry no real playback

        if content.lower().startswith("srt://"):
            entries.append({
                "start": start, "duration": duration, "end": start + duration,
                "type": "live", "path": None, "label": content,
            })
            continue

        if not content.upper().startswith("UTF8"):
            continue  # plain text marker like "STOP CCTV SIANG" — not real airtime

        raw_path = content[4:]
        cleaned = raw_path.replace("\\", "/")
        if len(cleaned) >= 2 and cleaned[1] == ":":
            cleaned = cleaned[2:]
        cleaned = cleaned.lstrip("/")

        try:
            full = safe_path_fn(cleaned)
        except ValueError:
            full = None

        if full and os.path.isfile(full):
            entries.append({
                "start": start, "duration": duration, "end": start + duration,
                "type": "video", "path": cleaned, "label": os.path.basename(cleaned),
            })
            continue

        basename = os.path.basename(cleaned)
        rel = filename_index.get(basename.lower())
        if rel:
            entries.append({
                "start": start, "duration": duration, "end": start + duration,
                "type": "video", "path": rel, "label": basename,
            })
        else:
            entries.append({
                "start": start, "duration": duration, "end": start + duration,
                "type": "missing", "path": None, "label": basename,
            })

    entries.sort(key=lambda e: e["start"])
    return entries


def load_precision_playlists():
    if not os.path.exists(PRECISION_FILE):
        return {}
    try:
        with open(PRECISION_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_precision_playlists(data):
    try:
        with open(PRECISION_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


class PrecisionScheduler:
    def __init__(self, controller, video_root, get_fallback_fn=None):
        self.controller = controller
        self.video_root = video_root
        self._get_fallback_fn = get_fallback_fn or (lambda: [])
        self._lock = threading.Lock()
        self.playlists = load_precision_playlists()  # {date_name: [entries]}
        self._current_entry_key = None  # (date, start) of what's loaded now
        self._switch_count = 0
        self._fallback_index = 0  # round-robin pointer for fallback list
        self._thread = None
        self._start_thread()

    # ---------- storage ----------

    def save_playlist(self, date_name, entries) -> str:
        name = (date_name or "").strip()
        if not name:
            raise ValueError("nama playlist tidak boleh kosong")
        with self._lock:
            self.playlists[date_name] = entries
            save_precision_playlists(self.playlists)
        return name

    def list_playlists(self):
        with self._lock:
            return {name: len(entries) for name, entries in self.playlists.items()}

    def get_playlist(self, date_name):
        """Kembalikan list entri rundown untuk satu tanggal, atau None."""
        with self._lock:
            entries = self.playlists.get(date_name)
            return list(entries) if entries is not None else None

    def delete_playlist(self, date_name):
        with self._lock:
            self.playlists.pop(date_name, None)
            save_precision_playlists(self.playlists)

    def status(self):
        with self._lock:
            # Deteksi awal tanpa entries untuk dapat tanggal siaran
            today_name = self._broadcast_date()
            today_entries = self.playlists.get(today_name, [])
            # Re-detect dengan entries agar anchor jam akurat
            if today_entries:
                today_name = self._broadcast_date(today_entries)
                today_entries = self.playlists.get(today_name, today_entries)
            now_sec = self._now_seconds(today_entries or None)
            current = self._find_active(today_entries, now_sec) if today_entries else None
            upcoming = None
            if today_entries:
                future = [e for e in today_entries if e["start"] > now_sec]
                if future:
                    upcoming = min(future, key=lambda e: e["start"])
            return {
                "enabled": True,
                "today": today_name,
                "has_schedule_today": bool(today_entries),
                "total_entries_today": len(today_entries),
                "current": current,
                "upcoming": upcoming,
                "switch_count": self._switch_count,
                "restart_every": self._restart_every(),
            }

    def _restart_every(self):
        value = getattr(self.controller, "chunk_size", PRECISION_RESTART_EVERY)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = PRECISION_RESTART_EVERY
        return max(1, value)

    def _pick_fallback(self, elapsed_in_slot=0):
        """Pilih fallback video secara round-robin.
        Setiap video diputar sekali sampai selesai, lalu otomatis lanjut
        ke video berikutnya (atau loop video yang sama jika cuma ada 1).
        Return full_path atau None jika tidak ada."""
        fallbacks = self._get_fallback_fn()
        if not fallbacks:
            return None
        return fallbacks[self._fallback_index % len(fallbacks)]

    def timeline(self, played_limit=5, upcoming_limit=15):
        """Daftar 'sudah diputar / sedang diputar / berikutnya' untuk
        panel 'Yang Sedang Berjalan di Player'. Perlu jalur data
        terpisah dari mpv sendiri: di Mode Presisi, mpv cuma pernah
        dimuati SATU file per saat (loadfile, bukan loadlist), jadi
        playlist bawaan mpv tidak pernah punya daftar 'berikutnya'
        yang berguna — daftar itu harus diturunkan dari jadwal
        presisi hari ini, bukan dari mpv."""
        with self._lock:
            today_name = self._broadcast_date()  # deteksi awal
            entries = list(self.playlists.get(today_name, []))
            if entries:
                today_name = self._broadcast_date(entries)  # re-detect dengan anchor akurat
                entries = list(self.playlists.get(today_name, entries))
        now_sec = self._now_seconds(entries or None)

        played, playing, upcoming = [], [], []
        for e in entries:
            # now_sec sudah dalam skala timecode broadcast (monoton melewati
            # tengah malam), jadi tidak perlu koreksi +86400 lagi.
            now_cmp = now_sec

            if e["type"] == "video":
                label = e["label"]
            elif e["type"] == "live":
                fallbacks = self._get_fallback_fn()
                if fallbacks:
                    label = f"📺 Fallback (slot live: {e['label'][:40]})"
                else:
                    label = "Segmen Live CCTV (belum didukung)"
            else:
                fallbacks = self._get_fallback_fn()
                if fallbacks:
                    label = f"📺 Fallback (file hilang: {e['label']})"
                else:
                    label = f"{e['label']} (file hilang)"
            item = {"name": f"{label} — {self._format_clock(e['start'])}"}

            if e["end"] <= now_cmp:
                played.append(item)
            elif e["start"] <= now_cmp < e["end"]:
                playing.append(item)
            else:
                upcoming.append(item)

        return {
            "played": played[-played_limit:] if played_limit else played,
            "playing": playing,
            "upcoming": upcoming[:upcoming_limit] if upcoming_limit else upcoming,
        }

    @staticmethod
    def _format_clock(total_seconds):
        total_seconds = int(total_seconds) % 86400  # bungkus balik jam lewat-tengah-malam
        h, rem = divmod(total_seconds, 3600)
        m = rem // 60
        return f"{h:02d}:{m:02d}"

    # ---------- engine internals ----------

    def _broadcast_start_hour(self, entries=None):
        """Tentukan jam anchor siaran secara otomatis.
        Prioritas:
          1. Entry paling awal di playlist aktif (auto-detect).
          2. settings.json key 'broadcast_start_hour'.
          3. _BROADCAST_START_HOUR_DEFAULT (6).
        """
        # 1. Auto-detect dari playlist
        if entries:
            min_start = min(e["start"] for e in entries)
            # min_start dalam detik timecode, misal 28800 = 08:00
            # Ambil jam-nya (floor), pastikan dalam rentang wajar 0–12
            detected = int(min_start // 3600)
            if 0 <= detected <= 12:
                return detected

        # 2. Dari settings.json
        try:
            settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
            with open(settings_path) as f:
                val = json.load(f).get("broadcast_start_hour")
            if val is not None:
                return int(val)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

        # 3. Default
        return _BROADCAST_START_HOUR_DEFAULT

    def _broadcast_date_and_seconds(self, entries=None):
        """Kembalikan (date_str, broadcast_seconds) di mana broadcast_seconds
        adalah jumlah detik sejak anchor siaran pada tanggal siaran.
        Jam anchor di-detect otomatis dari entry terdini di playlist.
        Contoh: siaran mulai jam 08:00, jam 01:30 WIB dianggap masih
        hari siaran kemarin."""
        anchor_hour = self._broadcast_start_hour(entries)
        now = datetime.now(WIB)
        anchor_today = now.replace(
            hour=anchor_hour, minute=0, second=0, microsecond=0
        )
        if now < anchor_today:
            anchor = anchor_today - timedelta(days=1)
        else:
            anchor = anchor_today
        elapsed = (now - anchor).total_seconds()
        date_str = anchor.strftime("%Y-%m-%d")
        return date_str, elapsed, anchor_hour

    def _now_seconds(self, entries=None):
        """Detik dalam skala timecode siaran (monoton melewati tengah malam).
        Contoh: jam 08:00 WIB dengan anchor 08:00 → 28800."""
        _, secs, anchor_hour = self._broadcast_date_and_seconds(entries)
        return secs + anchor_hour * 3600

    def _broadcast_date(self, entries=None):
        date_str, _, _ = self._broadcast_date_and_seconds(entries)
        return date_str

    @staticmethod
    def _find_active(entries, now_sec):
        """Cari entri yang sedang aktif. now_sec sudah monoton melewati
        tengah malam, sehingga langsung bisa dibandingkan dengan timecode."""
        for e in entries:
            if e["start"] <= now_sec < e["end"]:
                return {**e, "elapsed": now_sec - e["start"]}
        return None

    def _loop(self):
        while True:
            time.sleep(TICK_SECONDS)
            try:
                today_name = self._broadcast_date()  # deteksi awal tanpa entries
                with self._lock:
                    entries = self.playlists.get(today_name, [])
                if entries:
                    # Re-detect tanggal siaran dengan anchor dari playlist aktif
                    today_name = self._broadcast_date(entries)
                    with self._lock:
                        entries = self.playlists.get(today_name, [])
                if not entries:
                    # Tidak ada playlist hari ini — tetap putar fallback jika tersedia.
                    fallback_path = self._pick_fallback(0)
                    no_sched_key = (today_name, "no_schedule", fallback_path, self._fallback_index)

                    if self._current_entry_key == no_sched_key and self.controller.is_idle():
                        self._fallback_index += 1
                        fallback_path = self._pick_fallback(0)
                        no_sched_key = (today_name, "no_schedule", fallback_path, self._fallback_index)

                    if self._current_entry_key != no_sched_key:
                        if fallback_path:
                            self.controller.load_file_and_seek(fallback_path, 0, loop=False)
                            self._switch_count += 1
                        else:
                            self.controller.show_blank()
                        self._current_entry_key = no_sched_key
                    continue

                now_sec = self._now_seconds(entries)
                active = self._find_active(entries, now_sec)

                if active is None:
                    # Tidak ada entry yang cocok saat ini — bisa gap di tengah
                    # playlist atau playlist sudah selesai semuanya.
                    max_end = max(e["end"] for e in entries) if entries else 0
                    gap_reason = "end" if now_sec >= max_end else "gap"
                    fallback_path = self._pick_fallback(0)
                    gap_key = (today_name, gap_reason, fallback_path, self._fallback_index)

                    if self._current_entry_key == gap_key and self.controller.is_idle():
                        self._fallback_index += 1
                        fallback_path = self._pick_fallback(0)
                        gap_key = (today_name, gap_reason, fallback_path, self._fallback_index)

                    if self._current_entry_key != gap_key:
                        if fallback_path:
                            self.controller.load_file_and_seek(fallback_path, 0, loop=False)
                            self._switch_count += 1
                        else:
                            self.controller.show_blank()
                        self._current_entry_key = gap_key
                    continue

                key = (today_name, active["start"])
                if key == self._current_entry_key and self.controller.is_running():
                    continue  # already playing the right thing

                if active["type"] == "video":
                    full = os.path.join(self.video_root, active["path"])
                    self.controller.load_file_and_seek(full, float(active["elapsed"]))
                    self._switch_count += 1
                    self._current_entry_key = key
                else:
                    # live segment, missing file, or gap:
                    # try to play a fallback video instead of showing blank.
                    fallback_path = self._pick_fallback(active["elapsed"])
                    live_key = (today_name, active["start"], fallback_path, self._fallback_index)

                    if self._current_entry_key == live_key and self.controller.is_idle():
                        self._fallback_index += 1
                        fallback_path = self._pick_fallback(active["elapsed"])
                        live_key = (today_name, active["start"], fallback_path, self._fallback_index)

                    if self._current_entry_key != live_key:
                        if fallback_path:
                            self.controller.load_file_and_seek(fallback_path, 0, loop=False)
                            self._switch_count += 1
                        else:
                            self.controller.show_blank()
                        self._current_entry_key = live_key

                if self._switch_count >= self._restart_every():
                    self.controller.restart_process_only()
                    self._switch_count = 0
                    self._current_entry_key = None  # force reload next tick
            except Exception:
                # Never let the engine thread die silently.
                pass

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _start_thread(self):
        self.start()
