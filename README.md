# JITV Playout & Playlist Dashboard

Web dashboard dan automation playout engine untuk memutar dan menyiarkan playlist video secara terjadwal dan presisi di **Raspberry Pi 4** (atau server Linux/PC), baik melalui **output HDMI langsung (mpv)** maupun **streaming RTMP langsung (FFmpeg)**.

---

## 🌟 Fitur Utama

- **Dual Playback Backend (mpv & FFmpeg)**:
  - **mpv Engine**: Output display lokal via HDMI (DRM/KMS) dengan akselerasi hardware decoder (`v4l2m2m`) dan audio ALSA.
  - **FFmpeg Engine**: Direct RTMP streaming publisher tanpa perlu encoder hardware eksternal terpisah. Mendukung auto-reconnect/retry RTMP saat koneksi drop.
  - **Mock Engine**: Otomatis aktif saat dijalankan di Windows/lingkungan dev tanpa memerlukan hardware Raspberry Pi.
- **Mode Jadwal Presisi (Timecode-Accurate Playout)**:
  - Sinkronisasi berkelanjutan dengan jam dinding WIB (`Asia/Jakarta`).
  - Menghitung posisi tayang secara presisi: jika sistem baru aktif di tengah acara, otomatis langsung *seek* ke menit/detik yang sedang berlangsung (bukan dari 0:00).
  - Mengatur jeda waktu antar jadwal secara otomatis (*seamless transition*).
- **Impor Playlist Fleksibel**:
  - Mendukung arsip `.zip` berisi banyak file playlist harian, atau file mentah `.playlist`, `.ply`, dan `.txt`.
  - Otomatis melakukan indexing subfolder dan pencocokan path video di storage network/SMB.
  - Mendukung timecode presisi broadcast dan kompatibilitas format software otomasi lama.
- **Video Pengganti (Fallback Videos)**:
  - Panel konfigurasi video fallback/filler untuk mengisi slot Live CCTV, celah waktu (gap), atau video yang hilang/rusak agar siaran tidak menampilkan layar hitam kosong.
  - Pemutaran fallback secara *round-robin* / berulang (*looping*).
- **Proteksi Memory Leak (Anti-Leak Watcher)**:
  - Mengatasi bug hardware decoder Raspberry Pi (`v4l2m2m`) dengan mekanisme *silent auto-restart* berkala setiap *N* video (default 20, dapat diatur 5–100 dari dashboard/settings).
- **Logging Otomatis & Aman (Self-Cleaning Logs)**:
  - Handler log otomatis (`TruncatingFileHandler`) yang membatasi ukuran file log maks 5 MB per file (`log_scheduler.log`, `log_ffmpeg.log`, `log_mpv.log`) untuk mencegah SD Card Raspberry Pi penuh.
- **Tools Tambahan**:
  - `generate_playlist.py`: Generator file `.playlist` berbasis durasi riil video (`ffprobe`).
  - `tes_rtmp.py`: Script pengujian latensi TCP & bandwidth kecepatan upload ke server RTMP ingest.

---

## 🏗️ Arsitektur & Alur Kerja

```
[Sumber Video (SMB / Local)] 
           │
           ▼
[Playout Engine (app.py + precision_scheduler.py)]
     │                                    │
     ├─► Mode MPV (HDMI Lokal)            ├─► Mode FFmpeg (Direct RTMP)
     │   • mpv IPC Socket                 │   • libx264 / h264_v4l2m2m / copy
     │   • Output micro-HDMI Pi           │   • Push stream ke RTMP Ingest Server
     │   • Masuk Encoder Hardware         │     (MediaMTX / Nginx-RTMP / Cloud)
     │                                    │
     └───────────────────┬────────────────┘
                         ▼
             [VLC / Layar Monitor / Client]
```

---

## 📁 Struktur File

```
playlist-dashboard-jitv/
├── app.py                   # Flask server, routing API, & manajemen playlist/fallback
├── precision_scheduler.py   # Playout engine presisi (timecode sync WIB, fallback handler)
├── mpv_controller.py        # Controller mpv (IPC socket, DRM/KMS, Mock MPV dev)
├── ffmpeg_controller.py     # Controller FFmpeg (RTMP publisher, auto-retry, status tracker)
├── logger.py                # Logger mandiri dengan auto-truncate (maks 5MB)
├── generate_playlist.py     # CLI generator file .playlist via ffprobe
├── tes_rtmp.py              # Tool validasi latensi & bandwidth koneksi RTMP
├── fallback_videos.json     # Daftar video cadangan (fallback) aktif
├── settings.json            # Konfigurasi runtime (chunk_size, broadcast_start_hour)
├── requirements.txt         # Daftar dependency Python
├── raspi-dashboard.service  # Unit file systemd untuk auto-start di Linux/Raspberry Pi
├── templates/
│   ├── login.html           # Halaman login dashboard
│   └── dashboard.html       # Tampilan utama antarmuka kontrol siaran
└── static/
    ├── style.css            # Styling dark theme antarmuka
    └── app.js               # Logic frontend, status polling, render rundown & modal
```

---

## ⚙️ Konfigurasi Environment Variables

Semua konfigurasi dapat disetel via Environment Variable atau file `raspi-dashboard.service`:

| Variabel | Default | Keterangan |
|---|---|---|
| `DASHBOARD_PLAYER` | `mpv` | Backend playout: `mpv` (HDMI) atau `ffmpeg` (Direct RTMP). Di Windows otomatis mock jika `mpv`. |
| `DASHBOARD_RTMP_URL` | - | URL tujuan RTMP (Wajib diisi jika `DASHBOARD_PLAYER=ffmpeg`, mis. `rtmp://user:pass@ip:1935/live/streamkey`). |
| `DASHBOARD_ENCODER` | `libx264` | Codec video FFmpeg (`libx264`, `h264_v4l2m2m`, `copy`). |
| `DASHBOARD_FPS` | `25` | Output frame rate untuk streaming FFmpeg. |
| `CCTV_RTMP` | `rtmp://...` | Endpoint stream CCTV fallback bila ada entri `srt://` di playlist. |
| `DASHBOARD_USERNAME` | `admin` | Username login dashboard web. |
| `DASHBOARD_PASSWORD_HASH` | *hash changeme123* | Hash password login dashboard (**Wajib diganti**). |
| `DASHBOARD_SECRET_KEY` | *insecure default* | Secret key session Flask (**Wajib diganti**). |
| `DASHBOARD_VIDEO_ROOT` | `/mnt/videoserver` | Root direktori video / mount point Samba. |
| `DASHBOARD_CHUNK_SIZE` | `20` | Interval restart otomatis (jumlah video) untuk pencegahan leak RAM. |
| `DASHBOARD_MOCK` | `0` | Set `1` untuk memaksa Mock Controller di Linux. |

---

## 🚀 Panduan Instalasi & Menjalankan

### 1. Di Raspberry Pi (Produksi)

```bash
# 1. Clone / copy folder proyek ke Pi
git clone <repo-url> /home/eksan/raspi-dashboard
cd /home/eksan/raspi-dashboard

# 2. Buat virtual environment & install dependensi
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Pastikan mpv dan ffmpeg terpasang di sistem
sudo apt update
sudo apt install -y mpv ffmpeg
```

### 2. Generate Hash Password & Secret Key

Buat hash password login dan secret key baru:

```bash
source venv/bin/activate
# Generate Hash Password
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('PASSWORD_ANDA'))"

# Generate Secret Key
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Menjalankan Manual (Testing)

**Mode MPV (Output Layar / HDMI):**
```bash
export DASHBOARD_PLAYER="mpv"
export DASHBOARD_USERNAME="admin"
export DASHBOARD_PASSWORD_HASH='hasil-hash-di-atas'
export DASHBOARD_SECRET_KEY='hasil-secret-key-di-atas'
export DASHBOARD_VIDEO_ROOT="/mnt/videoserver"
python3 app.py
```

**Mode FFmpeg (Streaming RTMP Langsung):**
```bash
export DASHBOARD_PLAYER="ffmpeg"
export DASHBOARD_RTMP_URL="rtmp://user:pass@103.255.15.138:1935/live/demo"
export DASHBOARD_ENCODER="libx264"
export DASHBOARD_VIDEO_ROOT="/mnt/videoserver"
python3 app.py
```

Buka browser di `http://<ip-pi>:5000` atau `http://localhost:5000`.

---

## 🔄 Konfigurasi Otomatis Service (Systemd)

Untuk menjalankan dashboard otomatis saat Raspberry Pi menyala (*boot-up*):

1. Edit file `raspi-dashboard.service` dan sesuaikan nilainya:
   - `Environment=DASHBOARD_PASSWORD_HASH=...`
   - `Environment=DASHBOARD_SECRET_KEY=...`
   - `Environment=DASHBOARD_PLAYER=ffmpeg` (atau `mpv`)
   - `Environment=DASHBOARD_RTMP_URL=...`
   - `Environment=DASHBOARD_VIDEO_ROOT=/mnt/videoserver`

2. Pasang dan aktifkan service:
```bash
sudo cp raspi-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable raspi-dashboard
sudo systemctl start raspi-dashboard
```

3. Cek status dan log service:
```bash
sudo systemctl status raspi-dashboard
sudo journalctl -u raspi-dashboard -f
```

---

## 📖 Panduan Penggunaan Dashboard

### 1. Impor Jadwal Siaran (Playlist Harian)
1. Buka panel **Playlist Tersimpan**.
2. Klik **Impor Playlist Harian** dan pilih file jadwal harian (bisa file tunggal/multi `.playlist`, `.ply`, `.txt`, atau arsip `.zip`).
3. Format nama file (misal `2026_09_30.playlist`) otomatis terbaca sebagai jadwal tanggal `2026-09-30`.
4. Klik pada nama playlist di daftar untuk membuka modal **Rundown Siaran** (melihat tabel jam tayang, judul program, durasi, dan status siaran).

### 2. Konfigurasi Video Pengganti (Fallback)
1. Buka panel **📺 Video Pengganti**.
2. Klik **+ Tambah Video Pengganti** lalu jelajahi direktori dan pilih video.
3. Video fallback akan otomatis diputar bila slot jadwal berupa Live CCTV atau file video yang dijadwalkan tidak ditemukan di storage.

### 3. Monitoring Siaran (Queues & Timers)
- Panel **Yang Sedang Berjalan di Player** akan menampilkan kolom:
  - **Sudah Diputar**: Riwayat video yang telah selesai tayang.
  - **Sedang Diputar**: Video aktif beserta progress timer berjalan dan jam jadwal.
  - **Berikutnya**: Daftar antrean program tayang selanjutnya.

---

## 🛠️ Tools Pendukung

### 1. Generator File Playlist (`generate_playlist.py`)
Membuat file `.playlist` berstandar timecode presisi dari folder video lokal:
```powershell
python generate_playlist.py `
  --video-dir "D:\VideoAcara" `
  --start-time "06:00:00" `
  --date "2026-09-30" `
  --output "2026-09-30.playlist"
```

### 2. Validasi Jaringan & RTMP (`tes_rtmp.py`)
Menguji kelayakan latensi (TCP Ping) dan bandwidth upload sebelum streaming:
```bash
python tes_rtmp.py
```

---

## ⚠️ Catatan Penting & Troubleshooting

1. **Hardware Memory Leak (`v4l2m2m`)**:
   - Driver decoder hardware Raspberry Pi tidak selalu membebaskan memori 100% pada pergantian file. Dashboard secara otomatis me-restart engine player di background setiap 20 video (dapat disesuaikan di UI).
2. **Audio HDMI di Raspberry Pi**:
   - Jika menggunakan mode MPV tanpa suara di HDMI, pastikan baris berikut aktif di `/boot/firmware/config.txt` (lalu reboot):
     ```ini
     hdmi_force_hotplug=1
     hdmi_drive=2
     ```
3. **Zona Waktu Siaran**:
   - Semua jadwal dipatok ke **WIB (UTC+7)** secara internal di Python, sehingga tidak terpengaruh jika jam OS sistem berbeda.
4. **Keamanan & Reverse Proxy**:
   - Dashboard belum dilengkapi HTTPS bawaan. Jika diakses di luar jaringan lokal (LAN), disarankan menggunakan reverse proxy (Nginx/Caddy) dengan enkripsi TLS atau melalui SSH tunnel:
     ```bash
     ssh -p 8041 -L 5000:localhost:5000 eksan@103.131.105.122
     ```
