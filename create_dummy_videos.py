"""
create_dummy_videos.py — Dev utility (TIDAK masuk ke server Pi, ada di .gitignore)

Baca precision_playlists.json, lalu buat struktur folder + file video
0-byte tiruan di dummy_videos/ sehingga dashboard bisa dijalankan di
laptop Windows tanpa perlu mengunduh video asli JITV.

Cara pakai:
    python create_dummy_videos.py

Hasilnya: folder dummy_videos/ berisi file-file .mp4 kosong dengan
nama persis seperti video asli di server.  Sistem Playlist Dashboard
hanya mengecek os.path.isfile(), jadi file 0-byte sudah cukup.
"""
import json
import os

PRECISION_FILE = os.path.join(os.path.dirname(__file__), "precision_playlists.json")
PLAYLISTS_FILE = os.path.join(os.path.dirname(__file__), "playlists.json")
OUT_DIR = os.path.join(os.path.dirname(__file__), "dummy_videos")


def collect_labels():
    """Kumpulkan semua label video unik dari precision_playlists.json."""
    labels = set()
    try:
        with open(PRECISION_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for entries in data.values():
            for e in entries:
                label = e.get("label", "")
                typ   = e.get("type", "")
                # Skip segmen live (srt://) dan entri tanpa label
                if label and typ != "live":
                    labels.add(label)
    except (OSError, json.JSONDecodeError) as err:
        print(f"[WARN] Tidak bisa baca {PRECISION_FILE}: {err}")

    # Juga tambahkan nama dari playlists.json (path relatif)
    try:
        with open(PLAYLISTS_FILE, encoding="utf-8") as f:
            playlists = json.load(f)
        for items in playlists.values():
            for rel_path in items:
                labels.add(rel_path.replace("\\", "/"))
    except (OSError, json.JSONDecodeError):
        pass  # playlists.json bisa kosong/tidak ada — tidak apa-apa

    return labels


def make_dummy(labels):
    created = 0
    skipped = 0
    os.makedirs(OUT_DIR, exist_ok=True)

    for label in sorted(labels):
        # label bisa berupa path relatif (mis. "FOLDER/video.mp4")
        # atau nama file saja (mis. "video.mp4") dari precision_playlists
        rel = label.replace("\\", "/").lstrip("/")
        full = os.path.join(OUT_DIR, rel)

        if os.path.exists(full):
            skipped += 1
            continue

        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").close()  # file 0-byte
        created += 1

    return created, skipped


if __name__ == "__main__":
    print(f"Mengumpulkan nama video dari JSON...")
    labels = collect_labels()
    print(f"  -> {len(labels)} nama video unik ditemukan")

    print(f"Membuat file dummy di: {OUT_DIR}")
    created, skipped = make_dummy(labels)
    print(f"  -> {created} file dibuat, {skipped} sudah ada (dilewati)")
    print("Selesai! Set DASHBOARD_VIDEO_ROOT ke folder dummy_videos lalu jalankan run_local.ps1")
