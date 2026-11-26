import os
import re
import math
import subprocess
import json
import zipfile
import io
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from mpv_controller import make_controller, VIDEO_ROOT, ALLOWED_EXT, safe_path
from precision_scheduler import PrecisionScheduler, parse_playlist_text, CCTV_LIVE

app = Flask(__name__)

# --- Config (override via environment variables, see README) ---
app.secret_key = os.environ.get("DASHBOARD_SECRET_KEY", "please-change-this-secret-key")
ADMIN_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
# Default password is "changeme123" — CHANGE THIS before deploying.
# Generate a real hash with: python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('yourpassword'))"
ADMIN_PASSWORD_HASH = os.environ.get(
    "DASHBOARD_PASSWORD_HASH",
    generate_password_hash("changeme123"),
)

print(f"user : {ADMIN_USERNAME}", flush=True)
print(f"pass : {ADMIN_PASSWORD_HASH}", flush=True)

PLAYLIST_FILE = os.path.join(os.path.dirname(__file__), "playlist.json")
FALLBACK_FILE = os.path.join(os.path.dirname(__file__), "fallback_videos.json")

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
YOUTUBE_INGEST_URL = os.environ.get("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as file:
            settings = json.load(file)
        return settings if isinstance(settings, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings):
    temporary_path = SETTINGS_FILE + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(settings, file, indent=2)
        file.write("\n")
    os.replace(temporary_path, SETTINGS_FILE)


def is_valid_stream_url(url):
    if not isinstance(url, str) or not url:
        return False
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ("rtmp", "rtmps", "rtsp") and bool(parsed.netloc) and parsed.hostname is not None
    except ValueError:
        return False


def get_stream_output_config():
    config = load_settings().get("stream_output", {})
    if not isinstance(config, dict):
        config = {}
    mode = config.get("mode", "url")
    key = config.get("youtube_key", "")
    ingest = config.get("youtube_ingest_url", YOUTUBE_INGEST_URL)
    custom_url = config.get("custom_url", "")
    fallback_url = os.environ.get("RTMP_TARGET", "")
    if mode == "youtube" and isinstance(key, str) and key:
        active_url = f"{ingest}/{key}"
    elif is_valid_stream_url(custom_url):
        active_url = custom_url
    else:
        active_url = fallback_url if is_valid_stream_url(fallback_url) else ""
    return config, mode, key, custom_url, active_url


def redact_stream_url(url):
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    if not parts.password:
        return url
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, f"{parts.username or '***'}:***@{host}", parts.path, parts.query, parts.fragment))

_, _, _, _, startup_output_url = get_stream_output_config()
mpv = make_controller(rtmp_url=startup_output_url)

def resolve_playlist_paths(items):
    """Turn a list of relative paths into full paths, silently
    skipping anything that no longer exists (deleted/moved on the
    share since the playlist was saved)."""
    fullpaths = []
    for rel in items:
        try:
            full = safe_path(rel)
        except ValueError:
            continue
        if os.path.isfile(full):
            fullpaths.append(full)
    return fullpaths



def load_fallback_data():
    if not os.path.exists(FALLBACK_FILE):
        return []
    with open(FALLBACK_FILE) as f:
        return json.load(f)


def save_fallback_data(items):
    with open(FALLBACK_FILE, "w") as f:
        json.dump(items, f, indent=2)


def get_fallback_fullpaths():
    """Kembalikan list full path fallback video yang benar-benar ada."""
    result = []
    for rel in load_fallback_data():
        try:
            full = safe_path(rel)
        except ValueError:
            continue
        if os.path.isfile(full):
            result.append(full)
    return result


precision = PrecisionScheduler(mpv, VIDEO_ROOT, get_fallback_fullpaths)

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Username atau password salah"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/browse")
@login_required
def api_browse():
    """List folders and video files under VIDEO_ROOT/<path>.
    Sandboxed so a crafted path can't escape VIDEO_ROOT."""
    rel_path = request.args.get("path", "")
    query = request.args.get("q", "").strip().lower()
    try:
        full_dir = safe_path(rel_path)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400

    if not os.path.isdir(full_dir):
        return jsonify({"error": "not a directory"}), 404

    folders = []
    files = []
    if query:
        # Recursive search across the video library
        for dirpath, _dirnames, filenames in os.walk(full_dir):
            for name in filenames:
                if name.lower().endswith(ALLOWED_EXT) and query in name.lower():
                    rel = os.path.relpath(os.path.join(dirpath, name), VIDEO_ROOT)
                    files.append({"name": name, "path": rel.replace("\\", "/")})
    else:
        try:
            entries = sorted(os.listdir(full_dir), key=str.lower)
        except OSError as e:
            return jsonify({"error": str(e)}), 500

        for name in entries:
            full = os.path.join(full_dir, name)
            entry_rel = os.path.join(rel_path, name).replace("\\", "/").lstrip("/")
            if os.path.isdir(full):
                folders.append({"name": name, "path": entry_rel})
            elif os.path.isfile(full) and name.lower().endswith(ALLOWED_EXT):
                files.append({"name": name, "path": entry_rel})

    # breadcrumb segments for the current path
    parts = [p for p in rel_path.split("/") if p]
    crumbs = []
    accum = ""
    for p in parts:
        accum = f"{accum}/{p}".lstrip("/")
        crumbs.append({"name": p, "path": accum})

    return jsonify({
        "path": rel_path,
        "crumbs": crumbs,
        "folders": folders,
        "files": files,
    })


@app.route("/api/video-duration", methods=["POST"])
@login_required
def api_video_duration():
    """Read one selected video's duration without probing the whole library."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Request tidak valid"}), 400
    rel_path = data.get("path", "")
    if not isinstance(rel_path, str) or not rel_path.lower().endswith(ALLOWED_EXT):
        return jsonify({"error": "Format video tidak didukung"}), 400
    try:
        full_path = safe_path(rel_path)
    except (TypeError, ValueError):
        return jsonify({"error": "Path video tidak valid"}), 400
    if not os.path.isfile(full_path):
        return jsonify({"error": "File video tidak ditemukan"}), 404

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", full_path],
            capture_output=True, text=True, timeout=30, check=True,
        )
        duration = float(result.stdout.strip())
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Durasi tidak valid")
    except (OSError, subprocess.SubprocessError, ValueError):
        return jsonify({"error": "Durasi video tidak dapat dibaca"}), 422

    return jsonify({"duration": int(round(duration))})


@app.route("/api/import-playlist", methods=["POST"])
@login_required
def api_import_playlist():
    """Terima file .txt (1 nama/path video per baris), cocokkan tiap
    baris ke file yang benar-benar ada di VIDEO_ROOT. Baris bisa berupa
    nama file saja (dicari ke seluruh subfolder), path relatif persis,
    atau path gaya Windows lama (J:\\...) — supaya file lama semacam
    daftar dari .ply masih bisa dipakai ulang kalau perlu."""
    if "file" not in request.files:
        return jsonify({"error": "Tidak ada file yang diupload"}), 400

    uploaded = request.files["file"]
    try:
        raw_text = uploaded.read().decode("utf-8", errors="ignore")
    except Exception:
        return jsonify({"error": "Gagal membaca file, pastikan file teks (.txt)"}), 400

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return jsonify({"error": "File kosong"}), 400

    # Index semua file video di VIDEO_ROOT sekali jalan (nama file -> path
    # relatif), supaya baris yang cuma nama file saja tetap bisa ketemu
    # walau videonya ada di subfolder yang dalam.
    filename_index = {}
    for dirpath, _dirnames, filenames in os.walk(VIDEO_ROOT):
        for fname in filenames:
            if fname.lower().endswith(ALLOWED_EXT):
                rel = os.path.relpath(os.path.join(dirpath, fname), VIDEO_ROOT)
                rel = rel.replace("\\", "/")
                filename_index.setdefault(fname.lower(), rel)

    matched = []
    not_found = []
    for line in lines:
        cleaned = line.replace("\\", "/")
        if cleaned.lower().startswith("j:/"):
            cleaned = cleaned[3:]
        cleaned = cleaned.lstrip("/")

        # 1) coba sebagai path relatif persis dulu
        try:
            full = safe_path(cleaned)
        except ValueError:
            full = None
        if full and os.path.isfile(full):
            matched.append(cleaned)
            continue

        # 2) fallback: cocokkan cuma dari nama filenya, cari di seluruh folder
        basename = os.path.basename(cleaned)
        rel = filename_index.get(basename.lower())
        if rel:
            matched.append(rel)
        else:
            not_found.append(line)

    return jsonify({"matched": matched, "not_found": not_found})

@app.route("/api/status")
@login_required
def api_status():
    data = mpv.status()
        # mpv's own playlist selalu cuma 1 entri di Mode Presisi (lihat
        # docstring PrecisionScheduler.timeline) — pakai daftar
        # sudah-diputar/berikutnya dari jadwal presisi sbg gantinya.
    data["precision_timeline"] = precision.timeline()
    return jsonify(data)


# ---------- fallback videos (pengganti slot live CCTV / gap) ----------

@app.route("/api/fallback-videos", methods=["GET"])
@login_required
def api_get_fallback_videos():
    return jsonify(load_fallback_data())


@app.route("/api/fallback-videos", methods=["POST"])
@login_required
def api_set_fallback_videos():
    items = request.json.get("items", [])
    valid = []
    for rel in items:
        try:
            full = safe_path(rel)
        except ValueError:
            continue
        if os.path.isfile(full):
            valid.append(rel)
    save_fallback_data(valid)
    return jsonify({"status": "ok", "items": valid})


# ---------- stream output ----------
@app.route("/api/stream-output", methods=["GET"])
@login_required
def api_get_stream_output():
    config, mode, key, custom_url, active_url = get_stream_output_config()
    ingest = config.get("youtube_ingest_url", YOUTUBE_INGEST_URL)
    return jsonify({"mode": mode, "custom_url": custom_url, "youtube_key_set": bool(key), "active_url": redact_stream_url(active_url) if active_url else ""})


@app.route("/api/stream-output", methods=["POST"])
@login_required
def api_set_stream_output():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Format permintaan tidak valid"}), 400
    mode = payload.get("mode")
    key = payload.get("youtube_key", "")
    custom_url = payload.get("custom_url", "")
    if mode not in ("url", "youtube") or not isinstance(key, str) or not isinstance(custom_url, str):
        return jsonify({"error": "Konfigurasi output tidak valid"}), 400
    key, custom_url = key.strip(), custom_url.strip()
    if mode == "url" and custom_url and not is_valid_stream_url(custom_url):
        return jsonify({"error": "Masukkan URL RTMP, RTMPS, atau RTSP yang valid"}), 400
    if mode == "youtube" and (not key or len(key) > 256 or any(char.isspace() for char in key)):
        return jsonify({"error": "Stream key YouTube tidak valid"}), 400

    settings = load_settings()
    config = settings.get("stream_output", {})
    if not isinstance(config, dict):
        config = {}
    old_mode = config.get("mode", "url")
    old_key = config.get("youtube_key", "")
    ingest = os.environ.get("YOUTUBE_INGEST_URL", YOUTUBE_INGEST_URL).rstrip("/")
    old_active = f"{ingest}/{old_key}" if old_mode == "youtube" and old_key else get_stream_output_config()[4]
    active_url = f"{ingest}/{key}" if mode == "youtube" else custom_url
    config.update({"mode": mode, "youtube_key": key, "custom_url": custom_url, "youtube_ingest_url": ingest})
    settings["stream_output"] = config
    try:
        save_settings(settings)
        new_active_url = active_url if mode == "youtube" else (
            custom_url if is_valid_stream_url(custom_url) else os.environ.get("RTMP_TARGET", "")
        )
        if not is_valid_stream_url(new_active_url):
            new_active_url = ""
        restarted = False
        if new_active_url != old_active and new_active_url and hasattr(mpv, "change_output"):
            mpv.change_output(new_active_url)
            restarted = True
    except (OSError, RuntimeError) as error:
        return jsonify({"error": f"Gagal menyimpan atau mengganti publisher: {error}"}), 500
    return jsonify({"status": "ok", "publisher_restarted": restarted})


# ---------- named playlists----------

@app.route("/api/named-playlists", methods=["GET"])
@login_required
def api_list_named_playlists():
    return jsonify(precision.list_playlists())


@app.route("/api/named-playlists", methods=["POST"])
@login_required
def api_create_named_playlist():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    items = data.get('items', [])

    if not name:
        return jsonify({"error": "Nama playlist wajib diisi"}), 400

    if not re.match(r'^\d{4}-\d{2}-\d{2}$', name):
        return jsonify({"error": "Format nama harus yyyy-mm-dd"}), 400

    if not items:
        return jsonify({"error": "Playlist tidak boleh kosong"}), 400

    # Convert to precision playlist format
    precision_entries = []
    for item in items:
        entry_type = item.get('type', 'video')
        duration = item.get('duration')
        if entry_type in ('video', 'break', 'live') and (
            isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0
        ):
            return jsonify({"error": "Semua program harus memiliki durasi positif dalam detik"}), 400
        if entry_type == 'video':
            precision_entries.append({
                'start': time_to_seconds(item.get('start_time', '00:00:00')),
                'duration': duration,
                'label': item.get('name', ''),
                'path': item.get('path', ''),
                'type': 'video'
            })
        elif entry_type == 'break':
            return jsonify({'error': "Tipe item 'break' tidak didukung dalam playlist presisi"}), 400
        elif entry_type == 'live':
            precision_entries.append({
                'start': time_to_seconds(item.get('start_time', '00:00:00')),
                'duration': duration,
                'label': item.get('name', 'Live CCTV'),
                'type': 'live'
            })

    saved_name = precision.save_playlist(name, precision_entries)
    return jsonify({"status": "ok", "name": saved_name})


def time_to_seconds(time_str):
    """Convert HH:MM:SS to total seconds."""
    parts = time_str.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


@app.route("/api/named-playlists/<name>", methods=["DELETE"])
@login_required
def api_delete_named_playlist(name):
    precision.delete_playlist(name)
    return jsonify({"status": "ok"})


# Ekstensi file mentah (di luar .zip) yang diterima sebagai file
# .playlist langsung — beberapa sistem broadcast automation nyimpen
# file ini dengan ekstensi berbeda-beda walau isinya format yang sama.
RAW_PLAYLIST_EXT = (".playlist", ".ply", ".txt")


def _ingest_playlist_text(text, playlist_name, filename_index):
    """Logic inti (dipakai baik utk isi ZIP maupun file mentah): parse
    satu file *.playlist, simpan sbg Playlist Bernama (utk Jadwal biasa)
    + versi presisi dgn timecode asli (utk Mode Jadwal Presisi)."""
    matched = []
    not_found = []
    skipped_live = 0

    for line in text.splitlines():
        line = line.strip("\r\n")
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        content = fields[2].strip()
        if not content:
            continue
        if content.lower().startswith("srt://"):
            skipped_live += 1
            continue
        if content.upper().startswith("UTF8"):
            raw_path = content[4:]
        else:
            # Baris penanda acara (mis. "STOP CCTV SIANG"), bukan
            # video — lewati.
            continue

        cleaned = raw_path.replace("\\", "/")
        if len(cleaned) >= 2 and cleaned[1] == ":":
            cleaned = cleaned[2:]  # buang "J:" / "D:" dkk
        cleaned = cleaned.lstrip("/")

        try:
            full = safe_path(cleaned)
        except ValueError:
            full = None
        if full and os.path.isfile(full):
            matched.append(cleaned)
            continue

        basename = os.path.basename(cleaned)
        rel = filename_index.get(basename.lower())
        if rel:
            matched.append(rel)
        else:
            not_found.append(basename)

    saved_name = None

    # Sekalian bikin versi presisi (dengan timecode asli), dipakai
    # oleh Mode Jadwal Presisi — parsing ulang teks yang sama tapi
    # SEMUA baris (bukan cuma yang video), supaya timing gap-nya
    # ikut kebaca dengan benar.
    precision_entries = parse_playlist_text(text, VIDEO_ROOT, safe_path, filename_index)
    if precision_entries:
        saved_name = precision.save_playlist(playlist_name, precision_entries)

    return {
        "date": playlist_name,
        "playlist_name": saved_name,
        "matched_count": len(matched),
        "not_found_count": len(not_found),
        "not_found_sample": not_found[:5],
        "skipped_live_count": skipped_live,
    }


def _decode(raw_bytes):
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("utf-8", errors="ignore")


@app.route("/api/import-zip-playlists", methods=["POST"])
@login_required
def api_import_zip_playlists():
    """Terima satu atau lebih file: ZIP berisi banyak file *.playlist,
    ATAU file .playlist/.ply/.txt mentah langsung (satu atau beberapa
    sekaligus, tanpa perlu di-zip dulu). Format broadcast automation
    asli: 1 file per tanggal, timecode presisi + kemungkinan sisipan
    siaran live SRT. Untuk tiap file: ambil cuma urutan video-nya saja
    (skip baris live SRT, skip baris penanda acara seperti "STOP CCTV
    SIANG"), lalu simpan sebagai Playlist Bernama terpisah per tanggal
    — supaya bisa dijadwalkan manual lewat fitur Jadwal yang sudah ada."""
    uploads = request.files.getlist("file")
    if not uploads:
        return jsonify({"error": "Tidak ada file yang diupload"}), 400

    # Index nama file video sekali jalan (dipakai ulang untuk semua
    # file yang diupload, supaya tidak scan network share berkali-kali).
    filename_index = {}
    for dirpath, _dirnames, filenames in os.walk(VIDEO_ROOT):
        for fname in filenames:
            if fname.lower().endswith(ALLOWED_EXT):
                rel = os.path.relpath(os.path.join(dirpath, fname), VIDEO_ROOT)
                filename_index.setdefault(fname.lower(), rel.replace("\\", "/"))

    results = []
    errors = []

    for uploaded in uploads:
        fname_lower = uploaded.filename.lower()

        if fname_lower.endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(uploaded.read()))
            except zipfile.BadZipFile:
                errors.append(f"{uploaded.filename}: file ZIP tidak valid atau rusak")
                continue

            playlist_entries = [n for n in zf.namelist() if n.lower().endswith(RAW_PLAYLIST_EXT)]
            if not playlist_entries:
                errors.append(f"{uploaded.filename}: tidak ada file .playlist di dalamnya")
                continue

            for entry_name in sorted(playlist_entries):
                text = _decode(zf.read(entry_name))
                base = os.path.splitext(os.path.basename(entry_name))[0]
                playlist_name = base.replace("_", "-")
                results.append(_ingest_playlist_text(text, playlist_name, filename_index))

        elif fname_lower.endswith(RAW_PLAYLIST_EXT):
            text = _decode(uploaded.read())
            base = os.path.splitext(os.path.basename(uploaded.filename))[0]
            playlist_name = base.replace("_", "-")
            results.append(_ingest_playlist_text(text, playlist_name, filename_index))

        else:
            errors.append(
                f"{uploaded.filename}: format tidak dikenali "
                f"(dukung .zip, .playlist, .ply, .txt)"
            )

    if not results and errors:
        return jsonify({"error": "; ".join(errors)}), 400

    return jsonify({"results": results, "errors": errors})


@app.route("/api/precision/status")
@login_required
def api_precision_status():
    return jsonify(precision.status())


@app.route("/api/precision/playlists/<date_name>")
@login_required
def api_precision_playlist_detail(date_name):
    entries = precision.get_playlist(date_name)
    if entries is None:
        return jsonify({"error": "Jadwal tidak ditemukan"}), 404
    return jsonify(entries)

# ---------- Create Playlist ----------

@app.route("/create-playlist")
@login_required
def create_playlist():
    from precision_scheduler import CCTV_LIVE
    return render_template("form.html", cctv_live=CCTV_LIVE)


@app.route("/api/create-playlist", methods=["POST"])
@login_required
def api_save_created_playlist():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Format permintaan tidak valid"}), 400
    name = data.get("name")
    name = name.strip() if isinstance(name, str) else ""
    items = data.get("items")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
        return jsonify({"error": "Format nama harus yyyy-mm-dd"}), 400
    try:
        from datetime import date
        date.fromisoformat(name)
    except ValueError:
        return jsonify({"error": "Tanggal playlist tidak valid"}), 400
    if not isinstance(items, list) or not items:
        return jsonify({"error": "Playlist tidak boleh kosong"}), 400

    from generate_playlist import seconds_to_timecode
    rows = []
    precision_entries = []
    for item in items:
        if not isinstance(item, dict):
            return jsonify({"error": "Format item playlist tidak valid"}), 400
        kind = item.get("type", "video")
        duration = item.get("duration")
        start = item.get("start_time")
        if kind not in ("video", "live", "break"):
            return jsonify({"error": "Tipe item playlist tidak valid"}), 400
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            return jsonify({"error": "Semua program harus memiliki durasi positif dalam detik"}), 400
        try:
            if not isinstance(start, str) or not re.fullmatch(r"\d{2,}:\d{2}:\d{2}", start):
                raise ValueError
            hours, minutes, seconds = (int(part) for part in start.split(":"))
            if minutes > 59 or seconds > 59:
                raise ValueError
            start_seconds = hours * 3600 + minutes * 60 + seconds
            if start_seconds >= 172800:
                return jsonify({"error": "Jam mulai melebihi batas 48 jam"}), 400
        except (TypeError, ValueError):
            return jsonify({"error": "Jam mulai tidak valid"}), 400

        if kind == "video":
            rel_path = item.get("path")
            try:
                full_path = safe_path(rel_path)
            except (TypeError, ValueError):
                return jsonify({"error": "Path video tidak valid"}), 400
            if not os.path.isfile(full_path):
                return jsonify({"error": f"Video tidak ditemukan: {item.get('name', '')}"}), 400
            normalized_path = os.path.relpath(full_path, VIDEO_ROOT).replace(os.sep, "/")
            content = f"UTF8{normalized_path}"
            precision_entries.append({
                "start": start_seconds, "duration": duration, "end": start_seconds + duration,
                "label": os.path.basename(normalized_path), "path": normalized_path, "type": "video",
            })
        elif kind == "live":
            content = "srt://live"
            precision_entries.append({
                "start": start_seconds, "duration": duration, "end": start_seconds + duration,
                "label": CCTV_LIVE, "type": "live",
            })
        else:
            return jsonify({"error": "Tipe item 'break' tidak didukung dalam playlist presisi"}), 400

        rows.append((seconds_to_timecode(start_seconds), seconds_to_timecode(duration), content))

    if not rows:
        return jsonify({"error": "Playlist harus memiliki video atau segmen live"}), 400

    output_dir = os.path.join(os.path.dirname(__file__), "playlist")
    output_path = os.path.join(output_dir, f"{name}.playlist")
    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="\n") as playlist_file:
            for row in rows:
                playlist_file.write("\t".join(row) + "\n")
    except OSError as exc:
        return jsonify({"error": f"Gagal menyimpan file playlist: {exc}"}), 500

    try:
        precision.save_playlist(name, precision_entries)
    except (ValueError, OSError, TypeError) as exc:
        app.logger.exception("Gagal mendaftarkan jadwal playlist %s", name)
        return jsonify({"error": "Gagal mendaftarkan jadwal playlist"}), 500
    return jsonify({"status": "ok", "name": name, "path": f"playlist/{name}.playlist"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
