import os
import json
import zipfile
import io
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from mpv_controller import MPVController, MockMPVController, make_controller, VIDEO_ROOT, ALLOWED_EXT, safe_path
from scheduler import Scheduler
from precision_scheduler import PrecisionScheduler, parse_playlist_text

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

PLAYLIST_FILE = os.path.join(os.path.dirname(__file__), "playlist.json")

# make_controller() otomatis memilih MockMPVController di Windows (dev)
# dan MPVController asli di Raspberry Pi (produksi) — tidak perlu edit manual.
mpv = make_controller()



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


scheduler = Scheduler(mpv, resolve_playlist_paths)
precision = PrecisionScheduler(mpv, VIDEO_ROOT)
scheduler.is_precision_active = lambda: precision.enabled


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def load_playlist_data():
    if not os.path.exists(PLAYLIST_FILE):
        return []
    with open(PLAYLIST_FILE) as f:
        return json.load(f)


def save_playlist_data(items):
    with open(PLAYLIST_FILE, "w") as f:
        json.dump(items, f, indent=2)


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
    try:
        full_dir = safe_path(rel_path)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400

    if not os.path.isdir(full_dir):
        return jsonify({"error": "not a directory"}), 404

    folders = []
    files = []
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


@app.route("/api/playlist", methods=["GET"])
@login_required
def api_get_playlist():
    return jsonify(load_playlist_data())


@app.route("/api/playlist", methods=["POST"])
@login_required
def api_set_playlist():
    items = request.json.get("items", [])
    valid = []
    for rel in items:
        try:
            full = safe_path(rel)
        except ValueError:
            continue
        if os.path.isfile(full):
            valid.append(rel)
    save_playlist_data(valid)
    return jsonify({"status": "ok", "items": valid})


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


@app.route("/api/apply", methods=["POST"])
@login_required
def api_apply():
    items = load_playlist_data()
    fullpaths = resolve_playlist_paths(items)
    return jsonify(mpv.load_playlist(fullpaths))


@app.route("/api/play", methods=["POST"])
@login_required
def api_play():
    return jsonify(mpv.play())


@app.route("/api/pause", methods=["POST"])
@login_required
def api_pause():
    return jsonify(mpv.pause())


@app.route("/api/stop", methods=["POST"])
@login_required
def api_stop():
    return jsonify(mpv.stop())


@app.route("/api/next", methods=["POST"])
@login_required
def api_next():
    return jsonify(mpv.next())


@app.route("/api/prev", methods=["POST"])
@login_required
def api_prev():
    return jsonify(mpv.prev())


@app.route("/api/status")
@login_required
def api_status():
    data = mpv.status()
    if precision.enabled:
        # mpv's own playlist selalu cuma 1 entri di Mode Presisi (lihat
        # docstring PrecisionScheduler.timeline) — pakai daftar
        # sudah-diputar/berikutnya dari jadwal presisi sbg gantinya.
        p_status = precision.status()
        data["precision_timeline"] = precision.timeline()
        data["precision_switch_count"] = p_status["switch_count"]
        data["precision_restart_every"] = p_status["restart_every"]
    return jsonify(data)


@app.route("/api/settings", methods=["GET"])
@login_required
def api_get_settings():
    from mpv_controller import CHUNK_SIZE_MIN, CHUNK_SIZE_MAX
    return jsonify({
        "chunk_size": mpv.chunk_size,
        "chunk_size_min": CHUNK_SIZE_MIN,
        "chunk_size_max": CHUNK_SIZE_MAX,
    })


@app.route("/api/settings", methods=["POST"])
@login_required
def api_set_settings():
    from mpv_controller import CHUNK_SIZE_MIN, CHUNK_SIZE_MAX
    value = request.json.get("chunk_size")
    try:
        new_value = mpv.set_chunk_size(value)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({
        "chunk_size": new_value,
        "chunk_size_min": CHUNK_SIZE_MIN,
        "chunk_size_max": CHUNK_SIZE_MAX,
    })


# ---------- named playlists (used by the schedule) ----------

@app.route("/api/named-playlists", methods=["GET"])
@login_required
def api_list_named_playlists():
    return jsonify(scheduler.list_playlists())


@app.route("/api/named-playlists", methods=["POST"])
@login_required
def api_save_named_playlist():
    name = request.json.get("name", "")
    items = request.json.get("items", [])
    valid = [rel for rel in items if os.path.isfile(safe_path(rel))] if items else []
    try:
        saved_name = scheduler.save_playlist(name, valid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "name": saved_name, "items": valid})


@app.route("/api/named-playlists/<name>", methods=["DELETE"])
@login_required
def api_delete_named_playlist(name):
    scheduler.delete_playlist(name)
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
    if matched:
        saved_name = scheduler.save_playlist(playlist_name, matched)

    # Sekalian bikin versi presisi (dengan timecode asli), dipakai
    # oleh Mode Jadwal Presisi — parsing ulang teks yang sama tapi
    # SEMUA baris (bukan cuma yang video), supaya timing gap-nya
    # ikut kebaca dengan benar.
    precision_entries = parse_playlist_text(text, VIDEO_ROOT, safe_path, filename_index)
    if precision_entries:
        precision.save_playlist(playlist_name, precision_entries)

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


@app.route("/api/precision/enable", methods=["POST"])
@login_required
def api_precision_enable():
    precision.enable()
    return jsonify({"status": "ok", "enabled": True})


@app.route("/api/precision/disable", methods=["POST"])
@login_required
def api_precision_disable():
    precision.disable()
    return jsonify({"status": "ok", "enabled": False})


@app.route("/api/precision/playlists")
@login_required
def api_precision_playlists():
    return jsonify(precision.list_playlists())


@app.route("/api/precision/playlists/<date_name>")
@login_required
def api_precision_playlist_detail(date_name):
    entries = precision.get_playlist(date_name)
    if entries is None:
        return jsonify({"error": "Jadwal tidak ditemukan"}), 404
    return jsonify(entries)


# ---------- schedule ----------

@app.route("/api/schedule", methods=["GET"])
@login_required
def api_list_schedule():
    return jsonify(scheduler.status())


@app.route("/api/schedule", methods=["POST"])
@login_required
def api_add_schedule_entry():
    body = request.json or {}
    try:
        entry = scheduler.add_entry(
            time_str=body.get("time", ""),
            playlist_name=body.get("playlist_name", ""),
            recurrence=body.get("recurrence", ""),
            days=body.get("days"),
            date=body.get("date"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "entry": entry})


@app.route("/api/schedule/<entry_id>", methods=["DELETE"])
@login_required
def api_delete_schedule_entry(entry_id):
    scheduler.delete_entry(entry_id)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
