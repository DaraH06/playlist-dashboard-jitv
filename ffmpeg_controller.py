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
from datetime import datetime
from logger import ffmpeg_logger
# ---------------------------------------------------------------------------
# Konstanta / env
# ---------------------------------------------------------------------------

RTMP_URL     = os.environ.get("RTMP_TARGET", "")
ENCODER      = os.environ.get("DASHBOARD_ENCODER", "libx264")
FPS          = os.environ.get("DASHBOARD_FPS", "25")
WIDTH        = os.environ.get("DASHBOARD_WIDTH", "1920")
HEIGHT       = os.environ.get("DASHBOARD_HEIGHT", "1080")
BITRATE      = os.environ.get("DASHBOARD_BITRATE", "8500k")

# Re-use VIDEO_ROOT & helper utilities dari mpv_controller agar tidak duplikat.
from mpv_controller import (
    CHUNK_SIZE_MIN, CHUNK_SIZE_MAX,
    load_chunk_size, save_chunk_size, format_duration,
)

# Berapa detik tunggu sebelum retry saat FFmpeg gagal konek ke RTMP.
_RETRY_INTERVAL = 5
_MAX_RETRIES    = 3


# ---------------------------------------------------------------------------
# Helper: build FFmpeg commands
# ---------------------------------------------------------------------------

def _build_source_cmd(input_path, seek_seconds, encoder, fps, loop=False):
    """Source process: read file/stream -> normalize -> mpegts -> pipe:1."""
    is_stream = input_path.startswith(("rtmp://", "srt://", "http://", "https://"))
  
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]

    if loop and not is_stream:
        cmd += ["-stream_loop", "-1"]

    if not is_stream and seek_seconds and seek_seconds > 0:
        cmd += ["-ss", str(float(seek_seconds))]

    if is_stream:
        cmd += ["-fflags", "nobuffer", "-flags", "low_delay"]
        cmd += ["-i", input_path]
    else:
        cmd += [
            "-readrate", "1.0",
            "-readrate_initial_burst", "0.5",
            "-i", input_path
            ]

    # Normalization filter: scale & pad to target resolution, fix FPS
    vf = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    
    cmd += ["-vf", vf, "-r", fps]

    # Video encode
    if encoder == "copy":
        # Force encoding anyway because we need consistent MPEG-TS output for the publisher pipe
        # "copy" is not recommended for hybrid source switching.
        actual_encoder = "libx264"
    else:
        actual_encoder = encoder

    cmd += [
        "-c:v", actual_encoder,
        "-g", str(int(fps) * 2),
        "-b:v", BITRATE,
        "-maxrate", BITRATE,
        "-bufsize", str(int(BITRATE.replace('k','')) * 2) + "k",
    ]
    if actual_encoder == "libx264":
        cmd += ["-preset", "veryfast", "-tune", "zerolatency"]

    # Audio normalization
    cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-avoid_negative_ts", "make_zero"]

    # Output to MPEG-TS pipe
    cmd += ["-f", "mpegts", "pipe:1"]

    print(f"Source CMD:\n {' '.join(cmd)}", flush=True)
    return cmd


def _build_publisher_cmd(rtmp_url):
    """Publisher process: pipe:0 -> copy -> RTMP."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "mpegts",
        "-analyzeduration", "2000000", "-probesize", "5000000",
        "-i", "pipe:0",
        "-c", "copy",
        "-fflags", "+genpts",
        "-f", "flv", rtmp_url
    ]
    print(f"Publisher CMD:\n {' '.join(cmd)}", flush=True)
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

        # Gunakan RLock untuk menghindari deadlock saat fungsi internal saling panggil
        self._lock         = threading.RLock() 
        self._publisher_proc = None        # Persistent RTMP process
        self._source_proc    = None        # Transient file/stream source
        self._bridge_thread  = None
        self._stop_bridge    = False

        self._playlist     = []            # list full path
        self._index        = None          # index saat ini di playlist
        self._time_pos     = 0.0           # posisi saat ini (detik)
        self._paused       = False
        self._loop_file    = False
        self._last_error   = None          # string error terakhir / None
        self._chunk_progress = 0
        self._current_duration_sec = None
        self._current_duration_str = "--:--"
        self._watcher_started = False

        self._ensure_publisher()
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

    def _ensure_publisher(self):
        """Pastikan proses publisher RTMP berjalan."""
        with self._lock:
            if self._publisher_proc is not None and self._publisher_proc.poll() is None:
                return

            ffmpeg_logger.info(f"Starting Publisher to {self.rtmp_url}...")
            cmd = _build_publisher_cmd(self.rtmp_url)
            try:
                self._publisher_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=False, # binary pipe
                )
                
                # Monitor publisher stderr
                pub_ref = self._publisher_proc
                def _read_pub_stderr():
                    try:
                        for line in pub_ref.stderr:
                            l = line.decode('utf-8', errors='replace').rstrip()
                            if "error" in l.lower():
                                ffmpeg_logger.error(f"[FFmpeg Publisher Error] {l}")
                                with self._lock:
                                    self._last_error = f"[Pub] {l}"
                    except Exception: pass
                threading.Thread(target=_read_pub_stderr, daemon=True).start()

            except Exception as e:
                ffmpeg_logger.critical(f"CRITICAL: Failed to start publisher: {e}")
                self._last_error = f"Failed to start publisher: {e}"
                self._publisher_proc = None

    def _kill_source(self):
        """Hentikan proses source aktif."""
        if self._source_proc is None:
            return
        try:
            self._source_proc.terminate()
            self._source_proc.wait(timeout=1)
        except Exception:
            try:
                self._source_proc.kill()
            except Exception:
                pass
        self._source_proc = None

    def _kill_all(self):
        """Hentikan semua proses FFmpeg."""
        with self._lock:
            self._stop_bridge = True
            self._kill_source()
            if self._publisher_proc:
                try:
                    self._publisher_proc.stdin.close()
                    self._publisher_proc.terminate()
                    self._publisher_proc.wait(timeout=1)
                except Exception:
                    try: self._publisher_proc.kill()
                    except Exception: pass
                self._publisher_proc = None

    def _start_proc(self, input_path, seek_seconds=0, loop=False):
        """Mulai source baru. Publisher harus sudah ada atau akan dibuat."""
        # Note: self._lock is RLock, so calling _ensure_publisher is safe here
        self._ensure_publisher()
        
        with self._lock:
            self._kill_source()
            print(f"Starting Source: {input_path} (seek: {seek_seconds}s)", flush=True)
            cmd = _build_source_cmd(input_path, seek_seconds, self.encoder, self.fps, loop=loop)
            
            try:
                self._source_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=False,
                )
            except FileNotFoundError:
                ffmpeg_logger.error("ERROR: FFmpeg command not found!")
                self._last_error = "ffmpeg not found"
                return

            # Monitor source stderr
            src_ref = self._source_proc
            def _read_src_stderr():
                try:
                    for line in src_ref.stderr:
                        l = line.decode('utf-8', errors='replace').rstrip()
                        if "error" in l.lower() or "failed" in l.lower():
                            ffmpeg_logger.error(f"[FFmpeg Source Error] {l}")
                            with self._lock:
                                if self._source_proc is src_ref:
                                    self._last_error = f"[Src] {l}"
                except Exception: pass
            threading.Thread(target=_read_src_stderr, daemon=True).start()

            # Start bridge thread if not running
            if self._bridge_thread is None or not self._bridge_thread.is_alive():
                self._stop_bridge = False
                self._bridge_thread = threading.Thread(target=self._bridge_loop, daemon=True)
                self._bridge_thread.start()

    def _bridge_loop(self):
        """Jembatan data dari source.stdout ke publisher.stdin."""
        while not self._stop_bridge:
            src = None
            pub = None
            with self._lock:
                src = self._source_proc
                pub = self._publisher_proc
            
            if src and pub and src.poll() is None:
                try:
                    # Higher buffer for smooth playback
                    data = src.stdout.read(32768) 
                    if data:
                        try:
                            pub.stdin.write(data)
                            pub.stdin.flush()
                        except (BrokenPipeError, OSError):
                            ffmpeg_logger.info("Bridge: Publisher pipe broken, restarting...")
                            self._ensure_publisher()
                    else:
                        time.sleep(0.01)
                except Exception as e:
                    time.sleep(0.1)
            else:
                time.sleep(0.1)

    def is_running(self):
        """True jika publisher DAN source berjalan."""
        with self._lock:
            pub_ok = self._publisher_proc is not None and self._publisher_proc.poll() is None
            src_ok = self._source_proc is not None and self._source_proc.poll() is None
            return pub_ok and src_ok

    def is_idle(self):
        """Idle jika source mati atau playlist kosong."""
        with self._lock:
            src_ok = self._source_proc is not None and self._source_proc.poll() is None
            if src_ok:
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
            elif self._source_proc is None and self._index is not None and self._playlist:
                # Proses mati tapi state masih ada → restart.
                self._paused = False
                self._start_proc(self._playlist[self._index],
                                  seek_seconds=self._time_pos,
                                  loop=self._loop_file)
        return {"error": None}

    def load_file_and_seek(self, full_path, seek_seconds=0, loop=False, duration=None):
        """Muat satu file dan seek ke posisi tertentu (dipakai PrecisionScheduler, M7)."""
        with self._lock:
            self._playlist   = [full_path]
            self._index      = 0
            self._time_pos   = float(seek_seconds)
            self._paused     = False
            self._loop_file  = bool(loop)
            self._current_duration_sec = duration
            if duration is not None:
                self._current_duration_str = format_duration(duration)
            self._start_proc(full_path, seek_seconds=seek_seconds, loop=loop)

    def show_blank(self):
        """Hentikan playback (gap/missing file di precision mode)."""
        with self._lock:
            self._kill_source()
            self._index    = None
            self._time_pos = 0.0
            self._paused   = False

    def restart_process_only(self):
        """Kill source FFmpeg tanpa bookkeeping resume."""
        with self._lock:
            self._kill_source()

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
            src_alive = self._source_proc is not None and self._source_proc.poll() is None
            pub_alive = self._publisher_proc is not None and self._publisher_proc.poll() is None

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
                "running":              src_alive and pub_alive,
                "paused":               self._paused,
                "current_file":         current_file,
                "playlist_index":       self._index,
                "playlist_count":       len(self._playlist),
                "chunk_progress":       self._chunk_progress,
                "chunk_size":           self.chunk_size,
                "current_time_pos":     self._time_pos,
                "current_time_str":     format_duration(self._time_pos),
                "current_duration_sec": self._current_duration_sec,
                "current_duration_str": self._current_duration_str,
                "playlist":             items,
                # Field tambahan khusus FFmpeg
                "encoder":              self.encoder,
                "rtmp_url":             self.rtmp_url,
                "process_id":           self._source_proc.pid if self._source_proc else None,
                "publisher_id":         self._publisher_proc.pid if self._publisher_proc else None,
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
        1. Update _time_pos selama source berjalan.
        2. Deteksi source selesai -> auto-next.
        3. Pastikan publisher tetap hidup.
        """
        _last_tick = time.monotonic()
        _retry_count = 0

        while True:
            time.sleep(1)
            now = time.monotonic()
            elapsed = now - _last_tick
            _last_tick = now

            self._ensure_publisher()

            action = None
            exit_code = None

            with self._lock:
                if self._source_proc is None:
                    continue

                poll_result = self._source_proc.poll()

                if poll_result is None:
                    if not self._paused:
                        self._time_pos += elapsed
                    continue

                exit_code = poll_result
                self._source_proc = None

                if self._index is not None:
                    # Video selesai normal -> next
                    self._current_duration_sec = None
                    self._current_duration_str = "--:--"
                    if (not self._loop_file and self._index is not None
                            and self._index + 1 < len(self._playlist)):
                        action = ("next",)
                    elif self._loop_file and self._index is not None and self._playlist:
                        action = ("loop",)
                    else:
                        action = ("idle",)

            if action and action[0] == "next":
                with self._lock:
                    if (self._index is not None and self._index + 1 < len(self._playlist)):
                        self._index += 1
                        self._time_pos = 0.0
                        self._chunk_progress += 1
                        self._start_proc(self._playlist[self._index])

            elif action and action[0] == "loop":
                with self._lock:
                    if self._index is not None and self._playlist:
                        self._time_pos = 0.0
                        self._start_proc(self._playlist[self._index], loop=True)

            elif action and action[0] == "idle":
                with self._lock:
                    self._index = None
                    self._time_pos = 0.0
