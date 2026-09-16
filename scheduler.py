"""
Named playlists + a schedule that auto-applies a named playlist to the
player at a given time, either daily, on specific weekdays, or on one
specific calendar date.

This intentionally does NOT try to replicate the old .ply broadcast
system's second-by-second precision (hard-cutting a video mid-way to
hit the next slot exactly). It's simpler: at the scheduled time, the
matching playlist is loaded and starts from its first video — good
enough for "mulai jam segini, mainkan playlist ini", which is what was
asked for. If frame-accurate hard cuts are ever needed, that's a much
bigger feature (needs per-video duration tracking à la the .ply files).
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

# Jam dipatok ke WIB secara eksplisit (UTC+7).
try:
    from zoneinfo import ZoneInfo
    WIB = ZoneInfo("Asia/Jakarta")
except Exception:
    WIB = timezone(timedelta(hours=7))

PLAYLISTS_FILE = os.path.join(os.path.dirname(__file__), "playlists.json")
SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "schedule.json")

WEEKDAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


class Scheduler:
    def __init__(self, mpv_controller, resolve_paths, is_precision_active=None):
        """
        mpv_controller: the shared MPVController instance.
        resolve_paths: function(list[str relative paths]) -> list[str full paths],
                        skipping anything invalid/missing (reuses app.py's
                        existing safe_path + isfile checks).
        is_precision_active: optional callable -> bool. Daftar Jadwal
                        biasa dan Mode Jadwal Presisi mengendalikan mpv
                        yang sama — kalau presisi sedang aktif, entri
                        Daftar Jadwal yang jatuh tempo TIDAK boleh ikut
                        menimpa pemutaran (dua-duanya rebutan kontrol).
                        Entri tetap dianggap "sudah jalan" hari itu
                        (tidak akan nyusul nembak lagi nanti), cuma
                        eksekusinya yang di-skip.
        """
        self.mpv = mpv_controller
        self.resolve_paths = resolve_paths
        self.is_precision_active = is_precision_active
        self._lock = threading.Lock()
        self.playlists = _load_json(PLAYLISTS_FILE, {})
        self.schedule = _load_json(SCHEDULE_FILE, [])
        self._fired_today = {}  # entry_id -> "YYYY-MM-DD" it last fired on
        self._last_applied = None  # for the "next up" / status display
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- named playlists ----------

    def list_playlists(self):
        with self._lock:
            return {name: list(items) for name, items in self.playlists.items()}

    def save_playlist(self, name, items):
        name = (name or "").strip()
        if not name:
            raise ValueError("nama playlist tidak boleh kosong")
        with self._lock:
            self.playlists[name] = list(items)
            _save_json(PLAYLISTS_FILE, self.playlists)
        return name

    def delete_playlist(self, name):
        with self._lock:
            if name in self.playlists:
                del self.playlists[name]
                _save_json(PLAYLISTS_FILE, self.playlists)
            # Also drop any schedule entries that pointed at it — a
            # dangling entry would just silently never fire.
            self.schedule = [e for e in self.schedule if e.get("playlist_name") != name]
            _save_json(SCHEDULE_FILE, self.schedule)

    # ---------- schedule entries ----------

    def list_schedule(self):
        with self._lock:
            return list(self.schedule)

    def add_entry(self, time_str, playlist_name, recurrence, days=None, date=None):
        if playlist_name not in self.playlists:
            raise ValueError("playlist tidak ditemukan")
        try:
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            raise ValueError("format jam harus HH:MM")

        entry = {
            "id": uuid.uuid4().hex[:8],
            "time": time_str,
            "playlist_name": playlist_name,
            "recurrence": recurrence,
        }
        if recurrence == "days":
            days = [d for d in (days or []) if d in WEEKDAY_CODES]
            if not days:
                raise ValueError("pilih minimal satu hari")
            entry["days"] = days
        elif recurrence == "date":
            try:
                datetime.strptime(date or "", "%Y-%m-%d")
            except ValueError:
                raise ValueError("format tanggal harus YYYY-MM-DD")
            entry["date"] = date
        elif recurrence != "daily":
            raise ValueError("recurrence tidak dikenali")

        with self._lock:
            self.schedule.append(entry)
            self.schedule.sort(key=lambda e: e["time"])
            _save_json(SCHEDULE_FILE, self.schedule)
        return entry

    def delete_entry(self, entry_id):
        with self._lock:
            self.schedule = [e for e in self.schedule if e["id"] != entry_id]
            _save_json(SCHEDULE_FILE, self.schedule)

    def status(self):
        with self._lock:
            return {
                "schedule": list(self.schedule),
                "last_applied": self._last_applied,
            }

    # ---------- background loop ----------

    def _matches(self, entry, now, today_str, weekday_code):
        if entry["time"] != now.strftime("%H:%M"):
            return False
        recurrence = entry.get("recurrence")
        if recurrence == "daily":
            return True
        if recurrence == "days":
            return weekday_code in entry.get("days", [])
        if recurrence == "date":
            return entry.get("date") == today_str
        return False

    def _loop(self):
        while True:
            time.sleep(15)
            try:
                self._tick()
            except Exception:
                # A bad tick should never kill the scheduler thread.
                pass

    def _tick(self):
        now = datetime.now(WIB)
        today_str = now.strftime("%Y-%m-%d")
        weekday_code = WEEKDAY_CODES[now.weekday()]

        with self._lock:
            entries = list(self.schedule)
            playlists = dict(self.playlists)

        for entry in entries:
            if self._fired_today.get(entry["id"]) == today_str:
                continue
            if not self._matches(entry, now, today_str, weekday_code):
                continue

            # Tandai sudah "jalan" utk hari ini SEBELUM cek presisi,
            # supaya entri ini tidak nyusul nembak lagi nanti kalau
            # presisi baru dimatikan setelah jam ini lewat.
            self._fired_today[entry["id"]] = today_str

            if self.is_precision_active and self.is_precision_active():
                continue  # Mode Presisi aktif — biarkan dia yang pegang kendali mpv

            items = playlists.get(entry["playlist_name"], [])
            fullpaths = self.resolve_paths(items)
            if fullpaths:
                self.mpv.load_playlist(fullpaths)
                self._last_applied = {
                    "playlist_name": entry["playlist_name"],
                    "at": now.strftime("%Y-%m-%d %H:%M:%S"),
                }
