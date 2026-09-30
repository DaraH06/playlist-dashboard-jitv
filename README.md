# Raspi Playlist Dashboard (JITV Playout System)

Web dashboard otomatis berbasis **Mode Jadwal Presisi (Precision Playout)** untuk penyiaran video di Raspberry Pi maupun server streaming. Sistem ini dirancang untuk operasional 24/7: memutar program video sesuai timecode harian (WIB), menangani pergantian jadwal secara mandiri, mendukung video pengganti (*fallback*) untuk slot jeda atau Live CCTV, serta menyediakan pilihan *dual backend* player (**MPV** untuk output HDMI fisik atau **FFmpeg** untuk streaming RTMP langsung tanpa display).

---

## 1. Fitur Utama & Cara Kerja

- **Playout Otomatis Berbasis Jadwal Presisi (`PrecisionScheduler`)**:
  Penayangan berjalan otomatis mengikuti rundown harian yang diimpor dari file `.playlist`, `.ply`, `.txt`, atau arsip `.zip`. Jam tayang dicocokkan langsung dengan jam dinding (WIB). Jika sistem baru dinyalakan atau direstart di tengah jadwal, player otomatis langsung melompat (*seek*) ke detik penayangan yang seharusnya berjalan, bukan mulai dari awal (`0:00`).
- **Dukungan Dual Backend Player (`make_controller`)**:
  - **MPV (`DASHBOARD_PLAYER=mpv`)**: Mengendalikan instance MPV persisten via IPC socket (`/tmp/mpvsocket`). Digunakan pada Raspberry Pi yang terhubung via kabel HDMI ke hardware encoder eksternal. Di Windows (lingkungan dev lokal), sistem otomatis memakai *mock controller* bila MPV tidak dipasang.
  - **FFmpeg (`DASHBOARD_PLAYER=ffmpeg`)**: Menjalankan child process FFmpeg sebagai transcoder dan publisher RTMP langsung ke server target (`DASHBOARD_RTMP_URL`). Mode ini berjalan secara *headless* (tanpa display, tanpa kabel HDMI, dan tanpa hardware encoder eksternal terpisah).
- **Fitur Video Pengganti (*Fallback Videos*)**:
  Saat jadwal menemui slot **Live CCTV** (yang belum ada relay aktif), file video yang hilang (*missing*), atau jeda (*gap*) antar acara, sistem otomatis memutar daftar video pengganti secara bergantian (*round-robin*) agar layar siaran tidak gelap/blank.
- **Rundown Siaran Interaktif**:
  Operator dapat melihat rincian rundown siaran per tanggal melalui modal tabel di dashboard: jam tayang WIB, judul program, durasi, serta status kesiapan file (🟢 Siap, 🔴 Missing, 📡 Live).
- **Auto-Refresh Player (Mitigasi Memory Leak)**:
  Driver hardware decoder Raspberry Pi (`v4l2m2m`) memiliki karakteristik penumpukan RAM jika memutar puluhan video secara terus-menerus dalam satu proses. Sistem menyediakan fitur auto-restart player setiap `N` video (default 20, dapat diatur langsung dari dashboard). Setelah di-restart, scheduler langsung mengoreksi posisi playback dari jam dinding sehingga siaran tetap sinkron.

---

## 2. Instalasi di Raspberry Pi

```bash
# 1. Salin folder proyek ke Raspberry Pi (misal ke /home/eksan/raspi-dashboard)
scp -r raspi-dashboard eksan@<ip-pi>:/home/eksan/

# 2. SSH ke Raspberry Pi dan masuk ke folder proyek
ssh eksan@<ip-pi>
cd /home/eksan/raspi-dashboard

# 3. Buat virtual environment & install dependency
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Install MPV dan/atau FFmpeg sesuai backend yang akan digunakan
sudo apt update
sudo apt install -y mpv ffmpeg
```

---

## 3. Set Username & Password

Hindari menggunakan kredensial default. Generate password hash dengan script Python:

```bash
source venv/bin/activate
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('PASSWORD_KAMU'))"
```

Simpan string hash yang dihasilkan (format `scrypt:...` atau `pbkdf2:...`) ke environment variable `DASHBOARD_PASSWORD_HASH`.

---

## 4. Konfigurasi (Environment Variables)

Aplikasi membaca konfigurasi melalui environment variables:

| Variabel | Default | Keterangan |
|---|---|---|
| `DASHBOARD_USERNAME` | `admin` | Username login dashboard web |
| `DASHBOARD_PASSWORD_HASH` | (hash dari `changeme123`) | Hash password admin (**Wajib diganti**) |
| `DASHBOARD_SECRET_KEY` | `please-change-this-secret-key` | Secret key session Flask (**Wajib diganti**) |
| `DASHBOARD_VIDEO_ROOT` | `/mnt/videoserver` | Folder root pustaka video (lokal / mount Samba) |
| `DASHBOARD_CHUNK_SIZE` | `20` | Batas jumlah video sebelum proses player direfresh otomatis |
| `DASHBOARD_PLAYER` | (auto / `mpv`) | Backend player: `mpv` (HDMI/Mock) atau `ffmpeg` (RTMP headless) |
| `DASHBOARD_RTMP_URL` | `""` | URL RTMP tujuan publish (wajib diisi jika `DASHBOARD_PLAYER=ffmpeg`) |
| `DASHBOARD_ENCODER` | `libx264` | Video encoder untuk FFmpeg (`h264_v4l2m2m`, `libx264`, atau `copy`) |
| `DASHBOARD_FPS` | `25` | Output frame rate untuk streaming FFmpeg |
| `VIDEO_DELAY` | `3` | Delay buffer output FFmpeg dalam detik |
| `CCTV_RTMP` | `rtmp://172.16.16.222:1935/live/livecctv` | URL RTMP relay sumber live CCTV bila tersedia |

### Contoh Menjalankan Manual (Testing / Development):

**Mode MPV (Hardware/Display):**
```bash
export DASHBOARD_USERNAME="admin"
export DASHBOARD_PASSWORD_HASH='hasil-hash-password'
export DASHBOARD_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export DASHBOARD_VIDEO_ROOT="/mnt/videoserver"
export DASHBOARD_PLAYER="mpv"
python3 app.py
```

**Mode FFmpeg (RTMP Headless):**
```bash
export DASHBOARD_USERNAME="admin"
export DASHBOARD_PASSWORD_HASH='hasil-hash-password'
export DASHBOARD_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export DASHBOARD_VIDEO_ROOT="/mnt/videoserver"
export DASHBOARD_PLAYER="ffmpeg"
export DASHBOARD_RTMP_URL="rtmp://server-tujuan:1935/live/jitv"
export DASHBOARD_ENCODER="h264_v4l2m2m"
export DASHBOARD_FPS="25"
python3 app.py
```

Buka browser ke `http://<ip-perangkat>:5000`.

---

## 5. Panduan Operasional Dashboard

1. **Impor Jadwal Harian**:
   - Di panel **Playlist Tersimpan**, pilih file jadwal melalui tombol input.
   - Format yang didukung: file `.zip` (kumpulan jadwal beberapa hari), atau file tunggal/jamak `.playlist`, `.ply`, `.txt`.
   - Klik **Impor Playlist Harian**. Sistem akan membaca timecode siaran dan memetakan path video ke folder `DASHBOARD_VIDEO_ROOT`.
2. **Inspeksi Rundown Siaran**:
   - Klik tombol **👁 Rundown** pada daftar tanggal yang ada di panel Playlist Tersimpan.
   - Modal akan menampilkan seluruh daftar acara untuk tanggal tersebut beserta statusnya (🟢 Siap, 🔴 Missing jika file belum ada di disk, atau 📡 Live).
3. **Mengatur Video Pengganti (*Fallback Videos*)**:
   - Di panel **📺 Video Pengganti**, klik tombol **+ Tambah Video Pengganti**.
   - Telusuri folder pustaka video pada modal picker dan klik **Pilih** pada video yang diinginkan.
   - Video fallback akan otomatis diputar secara bergantian ketika jadwal berada pada slot jeda, missing file, atau live CCTV.
4. **Memantau Status Penayangan**:
   - Bagian atas dashboard menyajikan status player aktif, judul video, dan counter sisa video sebelum auto-restart.
   - Panel **Yang Sedang Berjalan di Player** menampilkan riwayat program yang *Sudah Diputar*, video yang *Sedang Diputar* (dilengkapi timer progress bar real-time), dan program *Berikutnya*.
5. **Mengatur Ambang Batas Refresh Player**:
   - Operator dapat mengubah nilai frekuensi refresh player (rentang 5–100 video) pada panel *Settings Bar* dan menekan tombol **Simpan**.

---

## 6. Uji Coba Playlist Presisi & Generator Lokal

Untuk membuat file jadwal presisi `.playlist` dari folder video lokal menggunakan durasi riil video (dibaca via `ffprobe`):

```powershell
python generate_playlist.py `
  --video-dir "D:\TestVideo" `
  --start-time "06:00:00" `
  --date "2026-09-23" `
  --output "2026-09-23.playlist"
```

Jika menguji penayangan FFmpeg secara lokal di Windows:
1. Jalankan server RTMP lokal seperti **MediaMTX**.
2. Set variabel environment:
   ```powershell
   $env:DASHBOARD_VIDEO_ROOT = "D:\TestVideo"
   $env:DASHBOARD_PLAYER = "ffmpeg"
   $env:DASHBOARD_RTMP_URL = "rtmp://127.0.0.1:1935/live/jitv"
   $env:DASHBOARD_ENCODER = "libx264"
   python app.py
   ```
3. Putar stream RTMP melalui VLC (`rtmp://127.0.0.1:1935/live/jitv`) atau WebRTC/HLS MediaMTX.

---

## 7. Menjalankan Otomatis Saat Boot (Systemd)

Service systemd memastikan dashboard dan background playout selalu aktif kembali setelah reboot:

1. Edit file `raspi-dashboard.service` sesuai konfigurasi server/Pi kamu:
   - Ganti `DASHBOARD_PASSWORD_HASH` dan `DASHBOARD_SECRET_KEY`.
   - Sesuaikan `DASHBOARD_VIDEO_ROOT` dengan mount point video (`/mnt/videoserver`).
   - Pilih backend `DASHBOARD_PLAYER=mpv` atau `DASHBOARD_PLAYER=ffmpeg`.
   - Jika memakai FFmpeg, isi `DASHBOARD_RTMP_URL` dan `DASHBOARD_ENCODER`.
2. Pasang dan aktifkan service:
   ```bash
   sudo cp raspi-dashboard.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable raspi-dashboard
   sudo systemctl restart raspi-dashboard
   ```
3. Cek status service:
   ```bash
   sudo systemctl status raspi-dashboard
   ```

---

## 8. Struktur File

```text
playlist-dashboard-jitv/
├── app.py                      # Server Flask, routing web & REST API
├── precision_scheduler.py      # Mesin utama jadwal presisi berbasis jam dinding WIB
├── mpv_controller.py           # Controller player MPV (IPC socket) + factory make_controller()
├── ffmpeg_controller.py        # Controller player FFmpeg headless + RTMP publisher
├── generate_playlist.py        # Script CLI pembuat .playlist presisi via ffprobe
├── requirements.txt            # Daftar pustaka Python yang dibutuhkan
├── raspi-dashboard.service     # Unit file systemd untuk auto-start Raspberry Pi
├── settings.json               # Konfigurasi persistensi lokal (ambang refresh player)
├── templates/
│   ├── login.html              # Template halaman login
│   └── dashboard.html          # Template halaman utama dashboard
└── static/
    ├── style.css               # Desain antarmuka dashboard (dark theme)
    └── app.js                  # Logika JavaScript frontend, polling status, & modal
```

---

## 9. Catatan & Keamanan

- **Format Video yang Didukung**: `.mp4`, `.mkv`, `.avi`, `.mov`, `.ts`, `.m4v`.
- **Path Sandboxing**: Penelusuran folder dibatasi hanya di dalam direktori `DASHBOARD_VIDEO_ROOT`. Upaya *path traversal* (seperti `../`) ditolak secara otomatis.
- **Kredensial & URL RTMP**: Jangan mencantumkan URL RTMP produksi, stream key, ataupun hash password ke dalam commit Git publik. Gunakan environment variables atau systemd drop-in override (`sudo systemctl edit raspi-dashboard`).
- **Akses Jaringan**: Dashboard belum memiliki enkripsi HTTPS bawaan. Jika diakses melalui jaringan publik atau internet, tempatkan aplikasi di belakang reverse proxy (seperti Nginx) dengan SSL/TLS atau gunakan SSH tunneling.
