"""
FFmpegController — backend playback berbasis FFmpeg + RTMP publisher.

Dijalankan saat DASHBOARD_PLAYER=ffmpeg. Interface-nya identik dengan
MPVController dan MockMPVController sehingga app.py tidak perlu diubah.

Desain utama
============
- Satu child process FFmpeg aktif pada satu waktu (dijaga oleh _lock).
- FFmpeg tidak bisa di-pause native → pause disimulasikan dengan menyimpan
  posisi terakhir, mematikan proses, lalu restart dari posisi itu saat play.
- Playlist sequential (M5): watcher thread deteksi FFmpeg selesai → auto next.
- Precision mode (M7): load_file_and_seek() dipanggil oleh PrecisionScheduler.
- Refresh (M8): restart_process_only() hitung ulang dari wall-clock.
- Retry RTMP (M9): max 3 percobaan, interval 5 detik.
- Thread safety: threading.Lock() melingkupi semua akses ke state dan proses.

Environment variables yang dibaca
==================================
  DASHBOARD_RTMP_URL   URL tujuan publish, wajib jika DASHBOARD_PLAYER=ffmpeg.
  DASHBOARD_ENCODER    Codec video (default: libx264).
  DASHBOARD_FPS        Frame rate output (default: 25).
  DASHBOARD_VIDEO_ROOT Root folder video (dipakai untuk validasi path).
"""
import os
import subprocess
import threading
import time

# ---------------------------------------------------------------------------
# Konstanta / env
# ---------------------------------------------------------------------------

RTMP_URL     = os.environ.get("DASHBOARD_RTMP_URL", "")
ENCODER      = os.environ.get("DASHBOARD_ENCODER", "libx264")
FPS          = os.environ.get("DASHBOARD_FPS", "25")

# Re-use VIDEO_ROOT & helper utilities dari mpv_controller agar tidak duplikat.
from mpv_controller import (
    VIDEO_ROOT, ALLOWED_EXT,
    CHUNK_SIZE_MIN, CHUNK_SIZE_MAX,
    load_chunk_size, save_chunk_size, format_duration,
)

# Berapa detik tunggu sebelum retry saat FFmpeg gagal konek ke RTMP.
_RETRY_INTERVAL = 5
_MAX_RETRIES    = 3


# ---------------------------------------------------------------------------
# Helper: build FFmpeg command
# ---------------------------------------------------------------------------

def _build_ffmpeg_cmd(input_path, seek_seconds, rtmp_url, encoder, fps, loop=False):
    """Bangun daftar argumen FFmpeg untuk publish satu file ke RTMP.

    seek_seconds > 0 → pakai -ss (input seeking, cepat untuk format yang support it).
    loop=True        → pakai -stream_loop -1 (fallback video mengulang terus).
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

    if loop:
        cmd += ["-stream_loop", "-1"]

    if seek_seconds and seek_seconds > 0:
        # Input seeking (-ss sebelum -i) jauh lebih cepat daripada output seeking,
        # dan cukup akurat untuk keperluan precision mode (toleransi ~1 detik).
        cmd += ["-ss", str(float(seek_seconds))]

    cmd += ["-re", "-i", input_path]

    # Video encode / copy
    if encoder == "copy":
        cmd += ["-c:v", "copy"]
    else:
        cmd += [
            "-c:v", encoder,
            "-r", fps,
            "-g", str(int(fps) * 2),  # keyframe interval = 2× FPS
            "-b:v", "2000k",
            "-maxrate", "2500k",
            "-bufsize", "5000k",
        ]
        if encoder == "libx264":
            cmd += ["-preset", "veryfast", "-tune", "zerolatency"]

    # Audio
    cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"]

    # Output
    cmd += ["-f", "flv", rtmp_url]
    return cmd


# ---------------------------------------------------------------------------
# FFmpegController
# ---------------------------------------------------------------------------

class FFmpegController:
    """Controller playback berbasis FFmpeg yang mempublish ke RTMP.

    Public API identik dengan MPVController dan MockMPVController.
    """

    def __init__(self, rtmp_url=None, encoder=None, fps=None):
        self.rtmp_url    = rtmp_url  or RTMP_URL
        self.encoder     = encoder   or ENCODER
        self.fps         = fps       or FPS
        self.chunk_size  = load_chunk_size()

        self._lock         = threading.Lock()
        self._proc         = None          # subprocess.Popen aktif
        self._playlist     = []            # list full path
        self._index        = None          # index saat ini di playlist
        self._time_pos     = 0.0           # posisi saat ini (detik)
        self._paused       = False
        self._loop_file    = False
        self._last_error   = None          # string error terakhir / None
        self._chunk_progress = 0
        self._watcher_started = False

        self._ensure_watcher()

    # ------------------------------------------------------------------
    # set_chunk_size (interface compatibility)
    # ------------------------------------------------------------------

    def set_chunk_size(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError("chunk size must be a whole number")
        if not (CHUNK_SIZE_MIN <= value <= CHUNK_SIZE_MAX):
            raise ValueError(f"chunk size must be between {CHUNK_SIZE_MIN} and {CHUNK_SIZE_MAX}")
        self.chunk_size = value
        self._chunk_progress = 0
        save_chunk_size(value)
        return self.chunk_size

    # ------------------------------------------------------------------
    # Process management
    # ------------------------------------------------------------------

    def _kill_proc(self):
        """Hentikan proses FFmpeg aktif. Dipanggil di bawah _lock."""
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception:
                pass
        self._proc = None

    def _start_proc(self, input_path, seek_seconds=0, loop=False):
        """Build command dan spawn FFmpeg. Dipanggil di bawah _lock.

        Mencatat stderr (non-blocking via thread) ke self._last_error.
        """
        self._kill_proc()
        cmd = _build_ffmpeg_cmd(input_path, seek_seconds, self.rtmp_url,
                                 self.encoder, self.fps, loop=loop)
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            self._last_error = "ffmpeg not found — pastikan ffmpeg ada di PATH"
            self._proc = None
            return

        self._last_error = None
        # Baca stderr di thread terpisah agar tidak blocking.
        proc_ref = self._proc
        def _read_stderr():
            lines = []
            try:
                for line in proc_ref.stderr:
                    lines.append(line.rstrip())
            except Exception:
                pass
            if lines:
                # Simpan hanya baris terakhir yang bermakna sebagai last_error
                # (biasanya baris error paling relevan ada di akhir).
                err_lines = [l for l in lines if l]
                if err_lines:
                    with self._lock:
                        if self._proc is proc_ref:  # masih proses yang sama
                            self._last_error = err_lines[-1]
        threading.Thread(target=_read_stderr, daemon=True).start()

    def is_running(self):
        """True jika ada proses FFmpeg aktif dan belum selesai."""
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def is_idle(self):
        """True jika tidak ada proses aktif atau playlist kosong."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return False
            return self._index is None or not self._playlist

    # ------------------------------------------------------------------
    # Public playback API
    # ------------------------------------------------------------------

    def load_playlist(self, filepaths):
        """Muat daftar file dan mulai dari awal (M5)."""
        if not filepaths:
            return {"error": "empty playlist"}
        with self._lock:
            self._playlist       = list(filepaths)
            self._index          = 0
            self._time_pos       = 0.0
            self._paused         = False
            self._loop_file      = False
            self._chunk_progress = 0
            self._start_proc(self._playlist[0])
        return {"error": None}

    def play(self):
        """Resume dari posisi tersimpan (jika pause) atau no-op jika sudah berjalan."""
        with self._lock:
            if self._paused and self._index is not None and self._playlist:
                # Restart FFmpeg dari posisi terakhir yang disimpan.
                self._paused = False
                self._start_proc(self._playlist[self._index],
                                  seek_seconds=self._time_pos,
                                  loop=self._loop_file)
            elif self._proc is None and self._index is not None and self._playlist:
                # Proses mati tapi state masih ada → restart.
                self._paused = False
                self._start_proc(self._playlist[self._index],
                                  seek_seconds=self._time_pos,
                                  loop=self._loop_file)
        return {"error": None}

    def pause(self):
        """Simpan posisi, matikan FFmpeg (FFmpeg tidak support pause native)."""
        with self._lock:
            if not self._paused and self._index is not None:
                self._paused = True
                self._kill_proc()
        return {"error": None}

    def stop(self):
        """Hentikan playback sepenuhnya."""
        with self._lock:
            self._kill_proc()
            self._index    = None
            self._time_pos = 0.0
            self._paused   = False
            self._loop_file = False
        return {"error": None}

    def next(self):
        """Pindah ke file berikutnya dalam playlist."""
        with self._lock:
            if self._index is None or not self._playlist:
                return {"error": "no playlist"}
            next_idx = self._index + 1
            if next_idx >= len(self._playlist):
                return {"error": "already at end"}
            self._index        = next_idx
            self._time_pos     = 0.0
            self._paused       = False
            self._chunk_progress += 1
            self._start_proc(self._playlist[self._index])
        return {"error": None}

    def prev(self):
        """Pindah ke file sebelumnya dalam playlist."""
        with self._lock:
            if self._index is None or not self._playlist:
                return {"error": "no playlist"}
            if self._index <= 0:
                return {"error": "already at start"}
            self._index    = self._index - 1
            self._time_pos = 0.0
            self._paused   = False
            self._start_proc(self._playlist[self._index])
        return {"error": None}

    def load_file_and_seek(self, full_path, seek_seconds=0, loop=False):
        """Muat satu file dan seek ke posisi tertentu (dipakai PrecisionScheduler, M7)."""
        with self._lock:
            self._playlist   = [full_path]
            self._index      = 0
            self._time_pos   = float(seek_seconds)
            self._paused     = False
            self._loop_file  = bool(loop)
            self._start_proc(full_path, seek_seconds=seek_seconds, loop=loop)

    def show_blank(self):
        """Hentikan playback (gap/missing file di precision mode)."""
        with self._lock:
            self._kill_proc()
            self._index    = None
            self._time_pos = 0.0
            self._paused   = False

    def restart_process_only(self):
        """Kill FFmpeg tanpa bookkeeping resume — precision scheduler akan
        re-derive posisi dari wall-clock pada tick berikutnya (M8)."""
        with self._lock:
            self._kill_proc()
            # Tidak restart di sini; PrecisionScheduler akan panggil
            # load_file_and_seek() lagi pada tick berikutnya.

    def get_playlist(self):
        """Kembalikan playlist dalam format yang kompatibel dengan MPVController."""
        with self._lock:
            return [
                {"filename": p, "current": (i == self._index)}
                for i, p in enumerate(self._playlist)
            ]

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self):
        """Kembalikan dict status kompatibel dengan consumer di app.py."""
        with self._lock:
            proc_alive = self._proc is not None and self._proc.poll() is None

            if self._index is None:
                items = []
            else:
                items = []
                for i, p in enumerate(self._playlist):
                    if i < self._index:
                        state = "played"
                    elif i == self._index:
                        state = "playing"
                    else:
                        state = "upcoming"
                    item = {"name": os.path.basename(p), "path": p, "state": state}
                    items.append(item)

            current_file = (
                self._playlist[self._index]
                if self._index is not None and self._playlist
                else None
            )

            return {
                "running":              proc_alive,
                "paused":               self._paused,
                "current_file":         current_file,
                "playlist_index":       self._index,
                "playlist_count":       len(self._playlist),
                "chunk_progress":       self._chunk_progress,
                "chunk_size":           self.chunk_size,
                "current_time_pos":     self._time_pos,
                "current_time_str":     format_duration(self._time_pos),
                "current_duration_sec": None,   # FFmpeg tidak expose durasi live
                "current_duration_str": "--:--",
                "playlist":             items,
                # Field tambahan khusus FFmpeg (ditampilkan di status)
                "encoder":              self.encoder,
                "rtmp_url":             self.rtmp_url,
                "process_id":           self._proc.pid if self._proc else None,
                "last_error":           self._last_error,
            }

    # ------------------------------------------------------------------
    # Background watcher (auto-next, time tracking, RTMP retry)
    # ------------------------------------------------------------------

    def _ensure_watcher(self):
        if self._watcher_started:
            return
        self._watcher_started = True
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def _watch_loop(self):
        """Loop background:
        1. Update _time_pos selama FFmpeg berjalan (estimasi dari elapsed).
        2. Deteksi FFmpeg selesai → auto-next (playlist sequential) atau retry (RTMP error).
        3. Tangani chunk refresh (M8).
        """
        _last_tick = time.monotonic()
        _retry_count = 0

        while True:
            time.sleep(1)
            now = time.monotonic()
            elapsed = now - _last_tick
            _last_tick = now

            with self._lock:
                proc_alive = self._proc is not None and self._proc.poll() is None

                # Update posisi estimasi saat FFmpeg berjalan
                if proc_alive and not self._paused:
                    self._time_pos += elapsed

                # Jika proses selesai dan tidak di-pause secara sengaja
                if not proc_alive and not self._paused and self._index is not None:
                    exit_code = self._proc.returncode if self._proc else None
                    self._proc = None

                    rtmp_error = (exit_code not in (0, None) and
                                  self._last_error and
                                  "rtmp" in (self._last_error or "").lower())

                    if rtmp_error and _retry_count < _MAX_RETRIES:
                        # RTMP disconnect/error → retry (M9)
                        _retry_count += 1
                        time.sleep(_RETRY_INTERVAL)
                        if (not self._paused and self._index is not None
                                and self._playlist):
                            self._start_proc(self._playlist[self._index],
                                             seek_seconds=self._time_pos,
                                             loop=self._loop_file)
                    else:
                        # Video selesai normal atau retry habis → next
                        _retry_count = 0
                        if (not self._loop_file and self._index is not None
                                and self._index + 1 < len(self._playlist)):
                            # Auto-next (M5)
                            self._index    += 1
                            self._time_pos  = 0.0
                            self._chunk_progress += 1
                            self._start_proc(self._playlist[self._index])
                        elif self._loop_file and self._index is not None and self._playlist:
                            # Loop file selesai → restart dari 0
                            self._time_pos = 0.0
                            self._start_proc(self._playlist[self._index], loop=True)
                        else:
                            # Playlist habis → idle
                            self._index    = None
                            self._time_pos = 0.0


# ---------------------------------------------------------------------------
# Self-check (jalankan: python ffmpeg_controller.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== FFmpegController self-check ===")
    errors = []

    # --- Test 1: state machine tanpa proses nyata ---
    c = FFmpegController(rtmp_url="rtmp://127.0.0.1:1935/live/test",
                         encoder="libx264", fps="25")

    # Simulasikan load_playlist tanpa spawn proses (override _start_proc)
    def _noop_start(path, seek_seconds=0, loop=False):
        pass
    c._start_proc = _noop_start  # monkey-patch untuk test

    c.load_playlist(["/fake/a.mp4", "/fake/b.mp4"])
    assert c._index == 0, f"index expected 0, got {c._index}"
    assert not c._paused

    c.pause()
    assert c._paused, "pause() harusnya set _paused=True"

    c.play()
    assert not c._paused, "play() harusnya clear _paused"

    c.next()
    assert c._index == 1, f"next() index expected 1, got {c._index}"

    c.prev()
    assert c._index == 0, f"prev() index expected 0, got {c._index}"

    c.stop()
    assert c._index is None, "stop() harusnya set _index=None"
    assert c.is_idle()

    print("  [OK] state machine: play/pause/stop/next/prev")

    # --- Test 2: set_chunk_size ---
    c.set_chunk_size(10)
    assert c.chunk_size == 10
    try:
        c.set_chunk_size(999)
        errors.append("set_chunk_size(999) harusnya raise ValueError")
    except ValueError:
        pass
    print("  [OK] set_chunk_size validation")

    # --- Test 3: _build_ffmpeg_cmd ---
    cmd = _build_ffmpeg_cmd("/video/a.mp4", 755, "rtmp://host/live/test",
                             "libx264", "25")
    assert "-ss" in cmd, "-ss harus ada saat seek_seconds > 0"
    assert "755.0" in cmd or "755" in cmd, "seek seconds harus ada di cmd"
    assert "rtmp://host/live/test" in cmd
    assert "libx264" in cmd
    print("  [OK] _build_ffmpeg_cmd (seek + encoder)")

    cmd_copy = _build_ffmpeg_cmd("/video/a.mp4", 0, "rtmp://host/live/test",
                                  "copy", "25")
    assert "copy" in cmd_copy
    assert "-ss" not in cmd_copy, "-ss tidak boleh ada jika seek_seconds=0"
    print("  [OK] _build_ffmpeg_cmd (copy, no seek)")

    # --- Test 4: status() format ---
    c2 = FFmpegController(rtmp_url="rtmp://127.0.0.1:1935/live/test")
    c2._start_proc = _noop_start
    c2.load_playlist(["/fake/a.mp4"])
    s = c2.status()
    for key in ("running", "paused", "current_file", "playlist_index",
                 "playlist_count", "chunk_progress", "chunk_size",
                 "current_time_pos", "current_time_str", "playlist",
                 "encoder", "rtmp_url", "last_error"):
        if key not in s:
            errors.append(f"status() missing key: {key}")
    print("  [OK] status() keys")

    if errors:
        print("\n[FAIL] Errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\n[PASS] Semua test lulus.")
