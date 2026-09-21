"""
Controls a persistent mpv instance via its JSON IPC socket, and guards
against the known Raspberry Pi v4l2m2m hardware-decoder memory leak
(confirmed by direct RSS measurement: memory climbs every track change
and never drops back down within one long-running process).

Instead of restarting mpv on every button click (the old nohup + pkill
setup) or never restarting it at all (unsafe for 24/7 playback), this
controller:
  - keeps ONE mpv process alive for normal play/pause/stop/next/prev,
    controlled entirely over the IPC socket (fast, no flicker), and
  - silently kills + respawns that process every CHUNK_SIZE tracks,
    then reloads the same playlist and jumps back to the same
    position, so memory is reclaimed before it becomes a problem.
    The dashboard/API surface looks continuous; only a short
    (sub-second) black flash happens at the DRM level during a chunk
    restart, exactly like the tested standalone chunking script.
"""
import socket
import json
import subprocess
import os
import time
import threading

MPV_SOCKET = "/tmp/mpvsocket"
# Real mount point used on this Pi (see README). Override with the
# DASHBOARD_VIDEO_ROOT env var if you deploy against a different path.
VIDEO_ROOT = os.environ.get("DASHBOARD_VIDEO_ROOT", "/mnt/videoserver")
ALLOWED_EXT = (".mp4", ".mkv", ".avi", ".mov", ".ts", ".m4v")

# How many tracks to play before silently restarting mpv to reclaim
# leaked hardware-decoder memory. This is the fallback default (used
# the very first time the app runs); after that, the value the user
# sets from the dashboard is persisted to SETTINGS_FILE and wins.
DEFAULT_CHUNK_SIZE = int(os.environ.get("DASHBOARD_CHUNK_SIZE", "20"))
CHUNK_SIZE_MIN = 5
CHUNK_SIZE_MAX = 100

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

M3U_PATH = "/tmp/dashboard_playlist.m3u"


def load_chunk_size():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                value = json.load(f).get("chunk_size")
            if isinstance(value, int) and CHUNK_SIZE_MIN <= value <= CHUNK_SIZE_MAX:
                return value
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_CHUNK_SIZE


def save_chunk_size(value):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"chunk_size": value}, f)
    except OSError:
        pass


def format_duration(seconds):
    if seconds is None:
        return "--:--"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def safe_path(rel_path):
    """Resolve rel_path under VIDEO_ROOT, refusing to escape it."""
    rel_path = (rel_path or "").lstrip("/")
    full = os.path.realpath(os.path.join(VIDEO_ROOT, rel_path))
    root = os.path.realpath(VIDEO_ROOT)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("path escapes VIDEO_ROOT")
    return full


class MPVController:
    def __init__(self, socket_path=MPV_SOCKET):
        self.socket_path = socket_path
        self.proc = None
        self._lock = threading.Lock()
        self._chunk_progress = 0
        self._last_seen_pos = None
        self._watcher_started = False
        self.chunk_size = load_chunk_size()
        # When True, the simple mode's chunk-restart watcher (which
        # tracks mpv's own playlist-pos) backs off entirely — precision
        # mode doesn't use mpv's playlist feature at all (it uses
        # "loadfile ... replace" per entry), so playlist-pos wouldn't
        # reflect anything meaningful here, and precision mode has its
        # own separate restart counter (see precision_scheduler.py).
        self.precision_mode_active = False

    def set_chunk_size(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError("chunk size must be a whole number")
        if not (CHUNK_SIZE_MIN <= value <= CHUNK_SIZE_MAX):
            raise ValueError(f"chunk size must be between {CHUNK_SIZE_MIN} and {CHUNK_SIZE_MAX}")
        self.chunk_size = value
        self._chunk_progress = 0  # start counting fresh against the new threshold
        save_chunk_size(value)
        return self.chunk_size

    # ---------- low-level process management ----------

    def is_running(self):
        if not os.path.exists(self.socket_path):
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(self.socket_path)
            s.close()
            return True
        except OSError:
            return False

    def _kill_existing(self):
        """Kill any mpv process bound to our socket, however it was
        started (including from a previous run of this app)."""
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        # Fallback: match by the distinguishing IPC socket argument,
        # rather than killing every mpv process on the system.
        subprocess.run(
            ["pkill", "-f", f"input-ipc-server={self.socket_path}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except OSError:
                pass
        time.sleep(1)

    def start(self):
        """Launch mpv in idle mode with the IPC socket enabled."""
        if self.is_running():
            # mpv survived a previous run of this app (e.g. we just
            # restarted the systemd service) — still make sure OUR
            # background chunk-restart watcher is running for it.
            # Without this, the watcher would only ever start the
            # first time this process spawns a brand new mpv, and
            # every subsequent app restart would silently leave a
            # long-lived mpv with no memory-leak safety net at all.
            self._ensure_watcher()
            return
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except OSError:
                pass
        cmd = [
            "mpv",
            "--idle=yes",
            f"--input-ipc-server={self.socket_path}",
            "--fullscreen",
            "--vo=gpu",
            "--gpu-context=drm",
            "--hwdec=v4l2m2m",
            "--ao=alsa",
            "--audio-device=alsa/plughw:1,0",
            "--cache=yes",
            "--demuxer-max-bytes=100M",
            "--demuxer-max-back-bytes=50M",
            "--loop-playlist=inf",
            "--no-terminal",
            "--really-quiet",
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(50):
            if os.path.exists(self.socket_path):
                time.sleep(0.3)
                self._ensure_watcher()
                return
            time.sleep(0.1)

    def _send(self, command):
        if not self.is_running():
            self.start()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            s.connect(self.socket_path)
            payload = json.dumps({"command": command}) + "\n"
            s.sendall(payload.encode())
            raw = s.recv(65536).decode()
            first_line = raw.splitlines()[0] if raw else "{}"
            return json.loads(first_line)
        except (OSError, json.JSONDecodeError) as e:
            return {"error": str(e)}
        finally:
            s.close()

    # ---------- playlist control ----------

    def load_playlist(self, filepaths):
        if not filepaths:
            return {"error": "empty playlist"}
        with self._lock:
            with open(M3U_PATH, "w") as f:
                for p in filepaths:
                    f.write(p + "\n")
            self._chunk_progress = 0
            self._last_seen_pos = None
            self._send(["loadlist", M3U_PATH, "replace"])
            return self._send(["set_property", "pause", False])

    def play(self):
        return self._send(["set_property", "pause", False])

    def pause(self):
        return self._send(["set_property", "pause", True])

    def stop(self):
        return self._send(["stop"])

    def next(self):
        return self._send(["playlist-next"])

    def prev(self):
        return self._send(["playlist-prev"])

    def get_playlist(self):
        res = self._send(["get_property", "playlist"])
        data = res.get("data")
        return data if isinstance(data, list) else []

    def status(self):
        if not self.is_running():
            return {"running": False, "playlist": []}
        pause = self._send(["get_property", "pause"])
        path = self._send(["get_property", "path"])
        pos = self._send(["get_property", "playlist-pos"])
        count = self._send(["get_property", "playlist-count"])
        # Live position/duration for whatever mpv currently has loaded —
        # this is the authoritative source for the "sedang diputar"
        # timer, since it reflects actual playback, not an estimate.
        time_pos = self._send(["get_property", "time-pos"])
        cur_duration = self._send(["get_property", "duration"])
        raw_playlist = self.get_playlist()

        current_index = pos.get("data")
        current_duration_sec = cur_duration.get("data")
        items = []
        for i, entry in enumerate(raw_playlist):
            filename = entry.get("filename", "")
            if current_index is None:
                state = "upcoming"
            elif i < current_index:
                state = "played"
            elif i == current_index:
                state = "playing"
            else:
                state = "upcoming"

            item = {
                "name": os.path.basename(filename),
                "path": filename,
                "state": state,
            }
            # Only the currently-playing item gets a duration — mpv
            # already knows it for the file it has open, no extra
            # probing needed. We deliberately do NOT do this for
            # upcoming/played items (that requires ffprobe-ing every
            # file on the network share, which caused real playback
            # stutter before — see README).
            if state == "playing" and current_duration_sec:
                item["duration_str"] = format_duration(current_duration_sec)
            items.append(item)

        time_pos_sec = time_pos.get("data")

        return {
            "running": True,
            "paused": pause.get("data"),
            "current_file": path.get("data"),
            "playlist_index": current_index,
            "playlist_count": count.get("data"),
            "chunk_progress": self._chunk_progress,
            "chunk_size": self.chunk_size,
            "current_time_pos": time_pos_sec,
            "current_time_str": format_duration(time_pos_sec),
            "current_duration_sec": current_duration_sec,
            "current_duration_str": format_duration(current_duration_sec),
            "playlist": items,
        }

    # ---------- background memory guard ----------

    def _restart_and_resume(self):
        """Kill + respawn mpv, reload the same playlist, and jump back
        to the track that was playing. Used periodically to reclaim
        memory the v4l2m2m decoder leaks across track changes."""
        with self._lock:
            pos_res = self._send(["get_property", "playlist-pos"])
            resume_index = pos_res.get("data") or 0
            self._kill_existing()
            self.start()
            if os.path.exists(M3U_PATH):
                self._send(["loadlist", M3U_PATH, "replace"])
                self._send(["set_property", "playlist-pos", resume_index])
                self._send(["set_property", "pause", False])
            self._chunk_progress = 0
            self._last_seen_pos = resume_index

    def _watch_loop(self):
        while True:
            time.sleep(5)
            try:
                if self.precision_mode_active:
                    continue
                if not self.is_running():
                    continue
                res = self._send(["get_property", "playlist-pos"])
                pos = res.get("data")
                if pos is None:
                    continue
                if self._last_seen_pos is None:
                    self._last_seen_pos = pos
                    continue
                if pos != self._last_seen_pos:
                    self._chunk_progress += 1
                    self._last_seen_pos = pos
                    if self._chunk_progress >= self.chunk_size:
                        self._restart_and_resume()
            except Exception:
                # Never let the watcher thread die silently.
                pass

    # ---------- direct file control (used by precision mode) ----------

    def load_file_and_seek(self, full_path, seek_seconds=0, loop=False):
        """Load a single file directly (not via mpv's playlist feature)
        and jump to the given position. Used by the precision scheduler
        to start a video exactly where it should be right now, instead
        of always starting from 0:00. Set loop=True for fallback videos
        that should repeat until the next scheduled slot starts."""
        self._send(["loadfile", full_path, "replace"])
        if seek_seconds and seek_seconds > 0:
            self._send(["seek", seek_seconds, "absolute"])
        self._send(["set_property", "loop-file", "inf" if loop else "no"])
        self._send(["set_property", "pause", False])

    def show_blank(self):
        """Stop playback entirely — used for gaps in the precision
        schedule, missing files, or live segments we don't support
        playing yet. mpv in idle mode with nothing loaded shows a
        plain black screen."""
        self._send(["stop"])

    def restart_process_only(self):
        """Kill mpv without trying to resume any playlist afterward —
        precision mode always re-derives what should be playing from
        the wall clock on its next tick, so no resume bookkeeping is
        needed here (unlike simple mode's _restart_and_resume)."""
        with self._lock:
            self._kill_existing()
            self.start()

    def _ensure_watcher(self):
        if self._watcher_started:
            return
        self._watcher_started = True
        t = threading.Thread(target=self._watch_loop, daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# Mock controller — dipakai otomatis di Windows (atau jika env var
# DASHBOARD_MOCK=1 di-set).  Public API identik dengan MPVController
# sehingga app.py tidak perlu berubah sama sekali saat di-deploy ke Pi.
# ---------------------------------------------------------------------------

class MockMPVController:
    """Simulasi in-memory MPVController untuk development lokal di Windows.

    Mensimulasikan:
    - is_running() selalu True setelah start()
    - load_playlist() / load_file_and_seek() menyimpan state di memori
    - status() mengembalikan JSON yang identik dengan controller asli
    - Timer posisi video terus berjalan (thread background)
    """

    def __init__(self, socket_path=None):
        self.chunk_size = load_chunk_size()
        self.precision_mode_active = False
        self._lock = threading.Lock()
        self._running = False
        self._paused  = True
        self._playlist = []      # list of full path strings
        self._index    = None    # current playlist index
        self._time_pos = 0.0     # detik saat ini di video
        self._duration = 0.0     # durasi video saat ini (0 jika tidak diketahui)
        self._chunk_progress = 0
        threading.Thread(target=self._tick, daemon=True).start()

    # --- timer background ---
    def _tick(self):
        while True:
            time.sleep(1)
            with self._lock:
                if self._running and not self._paused and self._duration > 0:
                    self._time_pos += 1
                    if self._time_pos >= self._duration:
                        # Pindah ke video berikutnya dalam playlist
                        if self._index is not None and self._index + 1 < len(self._playlist):
                            self._index   += 1
                            self._time_pos = 0.0
                            self._duration = 120.0  # durasi tiruan 2 menit per video
                            self._chunk_progress += 1
                        else:
                            # playlist habis — loop dari awal
                            if self._playlist:
                                self._index    = 0
                                self._time_pos = 0.0

    # --- process management (no-op on Windows) ---
    def is_running(self):
        return self._running

    def start(self):
        with self._lock:
            self._running = True

    def _kill_existing(self):
        pass  # tidak ada proses nyata untuk di-kill

    # --- playlist control ---
    def load_playlist(self, filepaths):
        if not filepaths:
            return {"error": "empty playlist"}
        with self._lock:
            self._playlist      = list(filepaths)
            self._index         = 0
            self._time_pos      = 0.0
            self._duration      = 120.0  # tiruan 2 menit per video
            self._paused        = False
            self._chunk_progress = 0
        return {"error": None}

    def play(self):
        with self._lock:
            self._paused = False
        return {"error": None}

    def pause(self):
        with self._lock:
            self._paused = True
        return {"error": None}

    def stop(self):
        with self._lock:
            self._index    = None
            self._time_pos = 0.0
            self._duration = 0.0
            self._paused   = True
        return {"error": None}

    def next(self):
        with self._lock:
            if self._index is not None and self._index + 1 < len(self._playlist):
                self._index   += 1
                self._time_pos = 0.0
                self._duration = 120.0
                self._chunk_progress += 1
        return {"error": None}

    def prev(self):
        with self._lock:
            if self._index is not None and self._index > 0:
                self._index   -= 1
                self._time_pos = 0.0
                self._duration = 120.0
        return {"error": None}

    def get_playlist(self):
        with self._lock:
            return [{"filename": p, "current": (i == self._index)}
                    for i, p in enumerate(self._playlist)]

    def status(self):
        with self._lock:
            if not self._running or self._index is None:
                return {"running": self._running, "playlist": []}

            items = []
            for i, p in enumerate(self._playlist):
                state = "played" if i < self._index else ("playing" if i == self._index else "upcoming")
                item = {"name": os.path.basename(p), "path": p, "state": state}
                if state == "playing" and self._duration:
                    item["duration_str"] = format_duration(self._duration)
                items.append(item)

            return {
                "running":               True,
                "paused":                self._paused,
                "current_file":          self._playlist[self._index] if self._index is not None else None,
                "playlist_index":        self._index,
                "playlist_count":        len(self._playlist),
                "chunk_progress":        self._chunk_progress,
                "chunk_size":            self.chunk_size,
                "current_time_pos":      self._time_pos,
                "current_time_str":      format_duration(self._time_pos),
                "current_duration_sec":  self._duration,
                "current_duration_str":  format_duration(self._duration),
                "playlist":              items,
            }

    def set_chunk_size(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError("chunk size must be a whole number")
        if not (CHUNK_SIZE_MIN <= value <= CHUNK_SIZE_MAX):
            raise ValueError(f"chunk size must be between {CHUNK_SIZE_MIN} and {CHUNK_SIZE_MAX}")
        self.chunk_size = value
        save_chunk_size(value)
        return self.chunk_size

    # --- direct file control (precision mode) ---
    def load_file_and_seek(self, full_path, seek_seconds=0, loop=False):
        with self._lock:
            self._playlist  = [full_path]
            self._index     = 0
            self._time_pos  = float(seek_seconds)
            self._duration  = 120.0
            self._paused    = False
            self._running   = True

    def show_blank(self):
        with self._lock:
            self._index    = None
            self._time_pos = 0.0
            self._duration = 0.0
            self._paused   = True

    def restart_process_only(self):
        pass  # tidak ada proses nyata


def make_controller():
    """Pilih controller yang tepat berdasarkan environment:
    - Windows atau DASHBOARD_MOCK=1 → MockMPVController (dev lokal)
    - Linux/Pi                      → MPVController asli (produksi)
    """
    if os.name == "nt" or os.environ.get("DASHBOARD_MOCK") == "1":
        return MockMPVController()
    return MPVController()
