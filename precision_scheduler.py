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
from datetime import datetime
from zoneinfo import ZoneInfo

# Jam dipatok eksplisit ke WIB, TIDAK ikut timezone sistem Pi — kalau
# jam sistem Pi kebetulan di-set UTC (default umum di Raspberry Pi OS
# yang belum dikonfigurasi), engine ini tetap jalan sesuai WIB, bukan
# ikut geser sesuai offset sistem.
WIB = ZoneInfo("Asia/Jakarta")

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
    def __init__(self, controller, video_root):
        self.controller = controller
        self.video_root = video_root
        self._lock = threading.Lock()
        self.playlists = load_precision_playlists()  # {date_name: [entries]}
        self.enabled = False
        self.active_date = None
        self._current_entry_key = None  # (date, start) of what's loaded now
        self._switch_count = 0
        self._thread = None
        self._start_thread()

    # ---------- storage ----------

    def save_playlist(self, date_name, entries):
        with self._lock:
            self.playlists[date_name] = entries
            save_precision_playlists(self.playlists)

    def list_playlists(self):
        with self._lock:
            return {name: len(entries) for name, entries in self.playlists.items()}

    def delete_playlist(self, date_name):
        with self._lock:
            self.playlists.pop(date_name, None)
            save_precision_playlists(self.playlists)

    # ---------- engine control ----------

    def enable(self):
        self.enabled = True
        self.controller.precision_mode_active = True

    def disable(self):
        self.enabled = False
        self.controller.precision_mode_active = False
        self._current_entry_key = None
        self.controller.show_blank()

    def status(self):
        with self._lock:
            today_name = datetime.now(WIB).strftime("%Y-%m-%d")
            today_entries = self.playlists.get(today_name, [])
            now_sec = self._now_seconds()
            current = self._find_active(today_entries, now_sec) if today_entries else None
            upcoming = None
            if today_entries:
                future = [e for e in today_entries if e["start"] > now_sec]
                if future:
                    upcoming = min(future, key=lambda e: e["start"])
            return {
                "enabled": self.enabled,
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

    def timeline(self, played_limit=5, upcoming_limit=15):
        """Daftar 'sudah diputar / sedang diputar / berikutnya' untuk
        panel 'Yang Sedang Berjalan di Player'. Perlu jalur data
        terpisah dari mpv sendiri: di Mode Presisi, mpv cuma pernah
        dimuati SATU file per saat (loadfile, bukan loadlist), jadi
        playlist bawaan mpv tidak pernah punya daftar 'berikutnya'
        yang berguna — daftar itu harus diturunkan dari jadwal
        presisi hari ini, bukan dari mpv."""
        with self._lock:
            today_name = datetime.now(WIB).strftime("%Y-%m-%d")
            entries = list(self.playlists.get(today_name, []))
        now_sec = self._now_seconds()

        played, playing, upcoming = [], [], []
        for e in entries:
            # Entri dgn start >= 24 jam = waktu setelah tengah malam yg
            # masih milik jadwal hari ini (lihat timecode_to_seconds) —
            # bandingkan ke "now + 24 jam" biar masuk kelompok yg benar.
            now_cmp = now_sec + 86400 if e["start"] >= 86400 else now_sec

            if e["type"] == "video":
                label = e["label"]
            elif e["type"] == "live":
                label = "Segmen Live CCTV (belum didukung)"
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

    @staticmethod
    def _now_seconds():
        now = datetime.now(WIB)
        return now.hour * 3600 + now.minute * 60 + now.second

    @staticmethod
    def _find_active(entries, now_sec):
        # Entries can be encoded with hours >= 24 for times after
        # midnight that still belong to "today's" schedule — so a
        # match against now_sec, or now_sec+86400 (for the "past
        # midnight" numbering), both count.
        for candidate in (now_sec, now_sec + 86400):
            for e in entries:
                if e["start"] <= candidate < e["end"]:
                    return {**e, "elapsed": candidate - e["start"]}
        return None

    def _loop(self):
        while True:
            time.sleep(TICK_SECONDS)
            if not self.enabled:
                continue
            try:
                today_name = datetime.now(WIB).strftime("%Y-%m-%d")
                with self._lock:
                    entries = self.playlists.get(today_name, [])
                if not entries:
                    if self._current_entry_key is not None:
                        self.controller.show_blank()
                        self._current_entry_key = None
                    continue

                now_sec = self._now_seconds()
                active = self._find_active(entries, now_sec)

                if active is None:
                    if self._current_entry_key is not None:
                        self.controller.show_blank()
                        self._current_entry_key = None
                    continue

                key = (today_name, active["start"])
                if key == self._current_entry_key:
                    continue  # already playing the right thing

                if active["type"] == "video":
                    full = os.path.join(self.video_root, active["path"])
                    self.controller.load_file_and_seek(full, active["elapsed"])
                    self._switch_count += 1
                else:
                    # missing file, live segment (not supported yet) —
                    # show blank instead.
                    self.controller.show_blank()

                self._current_entry_key = key

                if self._switch_count >= PRECISION_RESTART_EVERY:
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
