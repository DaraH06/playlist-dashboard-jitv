# Raspi Playlist Dashboard

Web dashboard buat kontrol player mpv di Raspberry Pi: play, pause, stop,
next/prev, susun playlist baru dari pustaka video (termasuk subfolder),
dan lihat apa yang sudah diputar / sedang diputar / berikutnya.

## Cara kerja

- Dashboard menyalakan **satu instance mpv** dalam mode `--idle` dengan
  IPC socket (`/tmp/mpvsocket`). Semua tombol (play/pause/stop/next/prev)
  mengirim perintah lewat socket itu — mpv **tidak di-kill/restart**
  setiap kali kamu klik tombol, jadi terasa instan.
- Playlist yang sedang kamu susun (belum diterapkan) disimpan di
  `playlist.json`. Tombol "Simpan & Terapkan" push daftar itu ke mpv.
- Panel "Yang Sedang Berjalan di Player" (Sudah Diputar / Sedang
  Diputar / Berikutnya) diambil **langsung dari playlist yang sedang
  di-load ke mpv**, bukan dari `playlist.json` — supaya tetap akurat
  walau kamu lagi menyusun playlist baru yang belum diterapkan.

### ⚠️ Kenapa ada "auto-restart" di background — WAJIB dibaca

Raspberry Pi 4 punya bug driver yang sudah terkonfirmasi lewat
pengukuran RAM langsung: hardware decoder (`v4l2m2m`) **tidak melepas
memori sepenuhnya** setiap kali mpv pindah video, meski masih dalam
satu proses yang sama. Kalau mpv dibiarkan hidup terus tanpa pernah
restart (seperti mode `--idle` di versi awal dashboard ini), RAM akan
naik terus sampai akhirnya Pi kehabisan memori dan macet total.

Untuk itu, `mpv_controller.py` sekarang punya **background watcher**
yang diam-diam me-restart proses mpv setiap `DASHBOARD_CHUNK_SIZE`
video (default 20), lalu otomatis reload playlist yang sama dan
lanjut dari posisi yang sama persis. Efeknya di dashboard: tidak
terasa apa-apa, cuma ada jeda hitam sepersekian detik di layar setiap
~20 video — jauh lebih baik daripada Pi hang karena kehabisan RAM.

Kalau di lapangan RAM masih terasa naik terus, turunkan nilainya:
```
DASHBOARD_CHUNK_SIZE=10
```
(env var, lihat bagian systemd service di bawah)

## Instalasi di Raspberry Pi

```bash
# 1. Copy folder ini ke Pi, misal ke /home/eksan/raspi-dashboard
scp -r raspi-dashboard eksan@<ip-pi>:/home/eksan/

# 2. SSH ke Pi, masuk folder
ssh eksan@<ip-pi>
cd raspi-dashboard

# 3. Buat virtual environment & install dependency
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Pastikan mpv terpasang
sudo apt install mpv
```

## Set username & password

Jangan pakai password default. Generate hash-nya dulu:

```bash
source venv/bin/activate
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('PASSWORD_KAMU'))"
```

## Konfigurasi (environment variables)

| Variabel | Default | Keterangan |
|---|---|---|
| `DASHBOARD_USERNAME` | `admin` | Username login dashboard |
| `DASHBOARD_PASSWORD_HASH` | (hash dari `changeme123`) | **Wajib diganti** sebelum deploy |
| `DASHBOARD_SECRET_KEY` | (nilai default, tidak aman) | **Wajib diganti**, generate dengan `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `DASHBOARD_VIDEO_ROOT` | `/mnt/videoserver` | Folder root video (sesuaikan dengan mount point di Pi kamu) |
| `DASHBOARD_CHUNK_SIZE` | `20` | Jumlah video sebelum mpv di-restart otomatis untuk mencegah RAM leak |

Untuk testing manual (bukan lewat systemd):

```bash
export DASHBOARD_USERNAME="admin"
export DASHBOARD_PASSWORD_HASH="hasil-hash-dari-command-di-atas"
export DASHBOARD_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export DASHBOARD_VIDEO_ROOT="/mnt/videoserver"
python3 app.py
```

Buka `http://<ip-pi>:5000` dari device yang satu jaringan dengan Pi.

## Uji playlist presisi dari video lokal

Generator [generate_playlist.py](generate_playlist.py) membuat file
`.playlist` berdasarkan durasi riil video yang dibaca oleh `ffprobe`.
Install FFmpeg di komputer pengujian dan pastikan `ffprobe` tersedia di
`PATH`, lalu jalankan:

```powershell
python generate_playlist.py `
  --video-dir "D:\TestVideo" `
  --start-time "06:00:00" `
  --date "2026-09-23" `
  --output "2026-09-23.playlist"
```

Set `DASHBOARD_VIDEO_ROOT` ke folder yang sama sebelum menjalankan
dashboard lokal. Import file `.playlist` melalui panel Mode Presisi.
Nama/path relatif dalam file playlist harus berada di bawah root tersebut.

Untuk tahap awal, uji dulu bahwa scheduler memuat video dan melakukan
seek ke posisi sesuai jam dinding. MediaMTX/RTMP merupakan tahap terpisah:
dashboard ini tidak mengirim output RTMP secara langsung. Setelah playback
lokal terbukti benar, gunakan FFmpeg atau encoder yang terpisah untuk
mengirim output ke endpoint MediaMTX, misalnya:

```text
rtmp://127.0.0.1:1935/live/test
```

URL playback MediaMTX biasanya:

```text
http://127.0.0.1:8889/live/test
```

Untuk produksi, ganti endpoint tersebut dengan URL RTMP server tujuan tanpa
mengubah format file `.playlist`.

## Jalan otomatis setelah reboot (disarankan)

Edit `raspi-dashboard.service`:
- Ganti `DASHBOARD_PASSWORD_HASH` dan `DASHBOARD_SECRET_KEY` dengan nilai asli kamu.
- Sesuaikan `DASHBOARD_VIDEO_ROOT` kalau mount point video-mu beda.
- Sesuaikan path `WorkingDirectory` dan `ExecStart` kalau folder-nya beda.

Lalu:

```bash
sudo cp raspi-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable raspi-dashboard
sudo systemctl start raspi-dashboard
```

Cek status: `sudo systemctl status raspi-dashboard`

Ini juga sekalian jadi solusi untuk masalah lama "player harus manual
restart setelah reboot" — dengan systemd, dashboard (dan mpv yang
di-spawn olehnya) akan otomatis jalan lagi setelah Pi restart.

⚠️ Jangan jalankan script player lama (`auto_player.sh` / `playlist_ply.sh`
yang pakai nohup) barengan dengan dashboard ini — dua proses mpv tidak
bisa rebutan akses DRM ke layar yang sama.

## Menyusun playlist

1. Buka panel **Pustaka Video** di kiri — bisa navigasi ke dalam
   subfolder (misal `FINAL JITV 2026 → TERAS JOGJA → 03. MARET`)
   lewat breadcrumb di atasnya.
2. Klik **+ Tambah** pada video yang mau dimasukkan ke playlist baru.
3. Atur urutan dengan tombol ↑ ↓ di panel **Playlist Baru**, atau
   hapus dengan ✕.
4. Klik **Simpan Playlist** (hanya simpan ke `playlist.json`, belum
   dikirim ke player), atau **Simpan & Terapkan ke Player** (langsung
   push ke mpv dan mulai main dari video pertama).
5. Panel **Yang Sedang Berjalan di Player** di bagian bawah otomatis
   update tiap 3 detik menunjukkan progres playlist yang sedang aktif.

## Jadwal otomatis (mainkan playlist sesuai jam & tanggal)

Supaya playlist tidak perlu diterapkan manual tiap hari, dashboard
punya fitur jadwal:

1. **Simpan playlist sebagai "Playlist Bernama"** — susun playlist
   seperti biasa di panel **Playlist Baru**, lalu isi nama di kolom
   bawahnya dan klik **Simpan sebagai Playlist Bernama** (misal
   `"Playlist Pagi"`). Playlist bernama ini terpisah dari playlist
   draft biasa dan khusus dipakai untuk penjadwalan.
2. Di panel **Jadwal Otomatis**, pilih playlist bernama tadi, isi jam
   mulai, dan pilih jenis pengulangan:
   - **Setiap hari** — jalan tiap hari di jam itu.
   - **Hari tertentu** — centang hari yang diinginkan (misal cuma
     Senin-Jumat).
   - **Tanggal spesifik** — untuk acara satu kali di tanggal tertentu
     (misal siaran khusus 17 Agustus). Jadwal tanggal spesifik ini
     **mengalahkan** jadwal berulang di hari yang sama.
3. Klik **Tambah Jadwal**. Background scheduler mengecek tiap 15 detik
   — begitu jamnya tiba, playlist otomatis diterapkan ke player tanpa
   perlu klik apa-apa.
4. Kalau ada beberapa jadwal yang jamnya sudah lewat di hari yang
   sama, yang dipakai adalah **jadwal dengan jam terbaru yang sudah
   terlewati** (sesuai desain "1 slot per hari" — begitu jadwal
   berikutnya tiba, otomatis pindah).

**Catatan:** fitur ini sengaja dibuat sederhana — begitu jamnya tiba,
playlist mulai dari video pertama (bukan hard-cut presisi detik
seperti sistem broadcast `.ply` yang lama). Untuk kebutuhan siaran
yang perlu presisi detik demi detik, itu di luar cakupan fitur ini.


## Struktur file

```
raspi-dashboard/
├── app.py                   # Flask app + routes
├── mpv_controller.py        # Kontrol mpv via IPC socket + auto-restart guard
├── requirements.txt
├── raspi-dashboard.service  # Systemd unit (opsional tapi disarankan)
├── templates/
│   ├── login.html
│   └── dashboard.html
└── static/
    ├── style.css
    └── app.js
```

## Catatan

- File yang muncul di Pustaka Video cuma yang berformat
  `.mp4 .mkv .avi .mov .ts .m4v` — sesuaikan `ALLOWED_EXT` di
  `mpv_controller.py` kalau perlu format lain.
- Path folder di-sandbox ke dalam `DASHBOARD_VIDEO_ROOT` — tidak bisa
  browse ke luar folder itu meski URL di-utak-atik manual.
- Dashboard ini tidak ada HTTPS bawaan — kalau diakses lewat jaringan
  yang lebih luas (bukan cuma LAN lokal), sebaiknya taruh di belakang
  reverse proxy (nginx) dengan TLS.
