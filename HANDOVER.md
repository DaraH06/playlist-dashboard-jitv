# Dokumentasi Handover — Sistem Playout Video JITV (Raspberry Pi & Headless)

Dokumen ini dibuat untuk serah terima proyek ke tim baru. Isinya mencakup
arsitektur sistem, infrastruktur, penjelasan source code, alur operasional,
dan yang terpenting — **riwayat masalah yang pernah terjadi beserta solusinya**,
supaya tim baru tidak perlu mengulang proses debugging yang sudah
dilakukan.

> **Update terbaru (September 2026 — Pembaruan Besar-besaran):**
> Sistem telah mengalami restrukturisasi dan modernisasi besar:
> 1. **Decommissioning Mode Sederhana:** Mode jadwal manual/sederhana (`scheduler.py`, `playlist.json`, `schedule.json`) telah **dihapus total**. Playout kini secara default dan murni digerakkan oleh **Mode Jadwal Presisi (`precision_scheduler.py`)**.
> 2. **Dukungan Dual Backend (MPV & FFmpeg):** Tersedia opsi backend **FFmpegController** (`ffmpeg_controller.py`) untuk penyiaran RTMP *headless* langsung ke server streaming tanpa memerlukan display, kabel HDMI, ataupun hardware encoder eksternal. Backend dipilih secara otomatis atau via env `DASHBOARD_PLAYER`.
> 3. **Fitur Video Pengganti (*Fallback Videos*):** Ditambahkan sistem video pengganti (`fallback_videos.json`) yang diputar secara *round-robin* saat jadwal memasuki slot jeda, missing file, atau live CCTV agar layar tidak gelap/blank.
> 4. **Relay Live CCTV Langsung:** Slot live CCTV (`srt://...`) kini dapat dialihkan langsung ke stream RTMP relay CCTV (`CCTV_RTMP`).
> 5. **Modal Rundown Siaran:** Penambahan tampilan modal tabel interaktif untuk memverifikasi jam tayang WIB, durasi, dan status file (🟢 Siap, 🔴 Missing, 📡 Live) sebelum siaran.
> 6. **Auto-Detect Anchor Jam Siaran:** Penjadwalan siaran lintas tengah malam (misal 06:00 s.d. 02:00 esok hari) kini otomatis mendeteksi jam anchor terawal dari playlist sehingga tidak salah berpindah tanggal.
> 7. **Generator Jadwal Otomatis:** Script `generate_playlist.py` ditambahkan untuk generate timecode presisi menggunakan `ffprobe`.

---

## 1. Ringkasan Proyek

Sistem ini berfungsi menyiarkan program siaran video secara otomatis dan terjadwal, lalu output video tersebut disiarkan (*streaming*) lewat protokol RTMP ke server pusat, sehingga bisa ditonton melalui VLC, pemutar video web, atau platform media lainnya.

Saat ini sistem mendukung **dua skenario arsitektur playout**:

### Arsitektur 1: Hardware Playout (MPV + Hardware Encoder HDMI — Eksisting)
```text
Raspberry Pi 4 (memutar video via MPV, output display HDMI)
        ↓ kabel micro-HDMI ke HDMI biasa
Hardware Encoder H.264 (172.16.100.225 — menangkap sinyal HDMI, encode jadi RTMP)
        ↓ jaringan (RTMP)
Server tujuan RTMP (103.255.15.138:1935 / server kantor)
        ↓
VLC / media player penonton
```

### Arsitektur 2: Headless Software Playout (FFmpegController — Arsitektur Baru)
```text
Raspberry Pi 4 / PC Server (FFmpegController decode & encode langsung)
        ↓ jaringan (RTMP publish langsung)
Server tujuan RTMP (103.255.15.138:1935 / MediaMTX lokal)
        ↓
VLC / HLS / WebRTC penonton
```
*Keunggulan Arsitektur 2:* Tidak membutuhkan monitor, kabel HDMI, maupun hardware encoder eksternal terpisah (*zero hardware dependency*).

Sistem playout sepenuhnya dikendalikan oleh **Dashboard Web** yang berjalan secara mandiri: membaca rundown siaran harian, menghitung posisi video berdasarkan jam dinding (WIB), dan menangani rotasi penayangan 24/7.

---

## 2. Infrastruktur & Cara Akses

### 2.1. Raspberry Pi (perangkat utama)
| Item | Nilai |
|---|---|
| Hostname | `playlist` |
| IP lokal (statis, tidak berubah) | `172.16.100.150` |
| Subnet mask | `255.255.255.0` |
| Gateway | `172.16.100.1` |
| DNS | `172.16.100.1`, `202.169.224.6`, `111.92.164.34` |
| Akses dari luar jaringan kantor | SSH lewat `103.131.105.122` port `8041` |
| User SSH | `eksan` |

**Cara SSH dari luar jaringan kantor:**
```bash
ssh -p 8041 eksan@103.131.105.122
```

**Catatan IP Statis:** IP Pi dikunci manual menggunakan `nmcli` agar tidak berubah setelah reboot, mencegah gagalnya koneksi mount dan socket player.

### 2.2. Sumber Video (Network Share / Samba)
| Item | Nilai |
|---|---|
| Server | `10.200.253.14` |
| Nama share | `data_jitv1` |
| Mount point di Pi | `/mnt/videoserver` |
| Metode mount | CIFS/SMB, via `/etc/fstab` + `systemd automount` |
| File kredensial | `/home/eksan/.smbcredentials` (permission `600`, hanya bisa dibaca user `eksan`) |

**Riwayat penting:** Server video pernah pindah IP dari `172.16.100.161` ke `10.200.253.14`. Jika video tidak bisa diakses (`Cannot open file`), cek status mount:
```bash
mount | grep videoserver
ping 10.200.253.14
```

### 2.3. Encoder Eksternal vs Backend FFmpeg
- **Jika memakai backend MPV (`DASHBOARD_PLAYER=mpv`):**
  Menggunakan Hardware Encoder di IP `172.16.100.225` (menu **Encoder → Main stream → RTMP PUBLISH URL**).
- **Jika memakai backend FFmpeg (`DASHBOARD_PLAYER=ffmpeg`):**
  Hardware encoder eksternal tidak digunakan. Raspberry Pi langsung meng-encode video (misal dengan hardware codec `h264_v4l2m2m`) dan mengirim stream RTMP ke alamat tujuan yang diatur pada env `DASHBOARD_RTMP_URL`.

### 2.4. Dashboard Web
| Item | Nilai |
|---|---|
| Lokasi di Pi | `/home/eksan/raspi-dashboard/` |
| Port | `5000` |
| Dikelola oleh | `systemd` service `raspi-dashboard.service` (auto-start saat boot) |
| Login default | Username `admin` |

**Cara akses dashboard dari luar jaringan kantor (SSH Tunneling):**
```bash
ssh -p 8041 -L 5000:localhost:5000 eksan@103.131.105.122
```
Biarkan terminal terbuka, lalu buka browser ke `http://localhost:5000`.

---

## 3. Penjelasan Source Code

Semua kode aplikasi berada di `/home/eksan/raspi-dashboard/` (atau direktori repo lokal):

| File | Fungsi |
|---|---|
| `app.py` | Aplikasi Flask utama — routing web, autentikasi session, REST API (`/api/status`, `/api/browse`, `/api/fallback-videos`, `/api/import-zip-playlists`, `/api/precision/playlists/<date>`) |
| `precision_scheduler.py` | **Mesin Playout Utama** — mengecek jam dinding WIB, auto-detect anchor jam siaran, memuat video sesuai timecode, seek ke posisi berjalan, rotasi fallback video round-robin, relay CCTV, dan auto-refresh player |
| `mpv_controller.py` | Controller player MPV via IPC socket (`/tmp/mpvsocket`), background watcher auto-restart mitigasi memory leak, transisi video mulus (*seamless*), serta fungsi factory `make_controller()` |
| `ffmpeg_controller.py` | Controller player FFmpeg *headless* — spawn child process FFmpeg, publish ke RTMP, simulasi pause/seek, tracking waktu, buffer delay, dan auto-retry koneksi RTMP |
| `generate_playlist.py` | Script CLI utilitas untuk membuat file `.playlist` presisi dari folder video lokal menggunakan pembacaan durasi riil `ffprobe` |
| `fallback_videos.json`* | Data persistensi daftar video pengganti (*fallback*) untuk slot jeda, file missing, atau live CCTV |
| `precision_playlists.json`* | Data persistensi jadwal harian per tanggal hasil impor file `.playlist`/`.ply`/`.zip`/`.txt` |
| `settings.json`* | Data persistensi konfigurasi dashboard (misalnya `chunk_size` untuk frekuensi refresh player) |
| `templates/dashboard.html` | Template HTML halaman dashboard (antarmuka presisi, fallback manager, status bar, modal rundown & picker) |
| `templates/login.html` | Template HTML halaman login |
| `static/app.js` | Logika frontend JavaScript — polling status, perataan timer lokal, rendering antarmuka, dan manajemen modal |
| `static/style.css` | Styling tema gelap (*dark theme*) yang responsif |
| `raspi-dashboard.service` | Unit file systemd untuk auto-booting di Raspberry Pi dengan konfigurasi environment lengkap |

*\*File-file JSON ini dibuat dan diperbarui otomatis oleh sistem.*

> **Catatan Pembersihan:** File legacy `scheduler.py` (Jadwal Sederhana), `playlist.json` (draft manual), dan `schedule.json` telah **dihapus total** dari repositori agar arsitektur bersih dan tidak ada lagi konflik antarscheduler.

---

## 4. Riwayat Masalah & Solusi (PALING PENTING — baca ini)

### 4.1. Bug hardware decoder Raspberry Pi (memory leak `v4l2m2m`)
**Gejala:** RAM terus naik selama playlist diputar, sampai akhirnya Raspberry Pi kehabisan memori dan hang total (perlu hard reset / cabut power).

**Root cause:** Chip decoder hardware di Raspberry Pi (`v4l2m2m`) **tidak melepas memori sepenuhnya** setiap kali video berganti dalam satu proses player yang sama (pola tangga naik pada RSS memori).

**Solusi yang diterapkan:** Proses player di-restart secara berkala setelah sejumlah `N` video (diatur lewat kolom "Refresh player otomatis tiap N video", default 20, rentang 5–100).
- Pada **MPV**: `MPVController` membunuh dan men-spawn ulang proses MPV, lalu reload file.
- Pada **Mode Presisi**: `PrecisionScheduler` memanggil `controller.restart_process_only()`, dan pada tick berikutnya (2 detik kemudian) scheduler secara otomatis menghitung ulang posisi video dari jam dinding WIB. Video langsung berlanjut di detik yang tepat tanpa perlu pencatatan manual state resume.

**Cara cek jika gejala muncul kembali:**
```bash
ps aux | grep -E 'mpv|ffmpeg'
```
Jika nilai kolom `RES` sudah melampaui 800MB–1GB dan tidak kunjung turun setelah melewati batas `N` video, periksa apakah ada proses zombie yang tertahan.

---

### 4.2. Lag video karena fitur pembacaan durasi (`ffprobe`)
**Gejala:** Video tersendat/lag di layar penonton saat fitur pembacaan durasi otomatis dijalankan.

**Root cause:** Pembacaan durasi video yang belum diputar lewat network share (SMB) menggunakan `ffprobe` memakan bandwidth jaringan dan I/O disk secara masif, berebut dengan player yang sedang membaca bitstream video aktif.

**Solusi yang diterapkan:**
1. Fitur inspeksi runtime `ffprobe` dihilangkan total dari proses playout aktif Raspberry Pi.
2. Pembacaan durasi dengan `ffprobe` kini hanya dijalankan **sekali di awal secara offline** oleh operator melalui script `generate_playlist.py` saat menyusun file jadwal `.playlist`.
3. Pada saat runtime, scheduler membaca durasi yang sudah tertulis rapi di file playlist tanpa perlu memanggil `ffprobe` ke storage.

---

### 4.3. IP server/perangkat berubah-ubah (DHCP)
**Gejala:** Mount `/mnt/videoserver` putus tiba-tiba atau SSH gagal.

**Solusi:** Raspberry Pi sudah dikunci ke IP statis `172.16.100.150` via `nmcli`. Pastikan server Samba di `10.200.253.14` juga memiliki IP reservasi/statis di jaringan datacenter.

---

### 4.4. Video di penonton tetap memutar walau player dihentikan (GOP Cache vs Headless)
**Gejala:** Klik Stop atau Pause di dashboard, tapi VLC penonton masih terus memutar cuplikan video.

**Penjelasan:**
- Pada **Arsitektur 1 (MPV + Hardware Encoder eksternal):** Sinyal HDMI berhenti, namun hardware encoder atau server RTMP masih menyisakan buffer GOP cache. Untuk memutus stream total, disable publish dari antarmuka web encoder (`172.16.100.225`).
- Pada **Arsitektur 2 (FFmpeg Headless):** Saat Stop dipanggil, proses FFmpeg langsung dihentikan (`SIGTERM`/`SIGKILL`), sehingga koneksi publisher RTMP ke server terputus seketika.

---

### 4.5. Password dengan karakter `$` gagal diekspor di bash
**Gejala:** Hash password terpotong saat diekspor melalui bash environment.

**Root cause:** Karakter `$` di dalam tanda kutip ganda (`"..."`) diinterpretasikan bash sebagai variabel shell.

**Solusi:** Selalu gunakan kutip tunggal (`'...'`) saat mendefinisikan string hash:
```bash
export DASHBOARD_PASSWORD_HASH='scrypt:32768:8:1$contoh_hash...'
```
Pada file `raspi-dashboard.service`, deklarasi `Environment=DASHBOARD_PASSWORD_HASH=scrypt:...` tidak memerlukan tanda kutip.

---

### 4.6. Video bersuara di laptop tetapi BISU di HDMI Raspberry Pi
**Gejala:** Video berjalan lancar di VLC melalui encoder eksternal, tetapi tidak ada audio sama sekali.

**Root cause:**
1. Parameter audio MPV di Raspberry Pi harus mengarah ke ALSA HDMI (`--ao=alsa --audio-device=alsa/plughw:1,0`).
2. Konfigurasi `/boot/firmware/config.txt` tidak memaksa mode HDMI penuh, sehingga port negosiasi ke mode DVI (video only tanpa jalur audio).

**Solusi:** Pastikan baris berikut aktif di `/boot/firmware/config.txt` Raspberry Pi:
```ini
hdmi_force_hotplug=1
hdmi_drive=2
```
*Catatan:* Pada backend FFmpeg (`DASHBOARD_PLAYER=ffmpeg`), masalah ini tidak terjadi karena FFmpeg meng-encode stream audio langsung ke AAC (`-c:a aac -b:a 128k`) di dalam stream multiplexing FLV/RTMP.

---

### 4.7. Format Playlist Timecode Broadcast (`.ply` / `.playlist`)
**Status:** Format broadcast automation harian berbasis tab-delimited timecode (`HH:MM:SS:FF`) kini menjadi **standar utama penyiaran**.
Format ini memuat:
`[Jam Mulai] \t [Durasi] \t [Tipe/Path Video]`
Contoh baris:
```text
06:00:00:00    00:24:30:00    UTF8FINAL JITV 2026/TERAS JOGJA/eps01.mp4
06:24:30:00    00:05:00:00    srt://172.16.16.222:9000
```
Dashboard menerima file format ini baik dalam bentuk tunggal maupun dikompresi dalam arsip `.zip`.

---

### 4.8. Jadwal meleset jamnya karena perbedaan timezone sistem
**Gejala:** Video tayang bergeser beberapa jam secara konsisten.

**Root cause:** Python menggunakan jam lokal sistem yang di beberapa instalasi Pi OS belum diatur ke WIB (default UTC).

**Solusi:** `precision_scheduler.py` mematok zona waktu secara eksplisit menggunakan `zoneinfo.ZoneInfo("Asia/Jakarta")` (atau fallback timezone offset UTC+7). Jadwal selalu berjalan tepat waktu WIB tanpa terpengaruh konfigurasi timezone OS host.

---

### 4.9. Konflik antar-scheduler (Terselesaikan Permanen)
**Gejala Masa Lalu:** Jadwal sederhana menimpa dan memotong pemutaran mode presisi.

**Solusi:** Modul `scheduler.py` dan seluruh antarmuka pembuatan playlist manual telah **dihapus total dari sistem**. Kini hanya ada satu pengendali jadwal tunggal: `PrecisionScheduler`.

---

### 4.10. Panel status antrean kosong di Mode Presisi (Terselesaikan)
**Gejala Masa Lalu:** Kolom "Berikutnya" selalu bertuliskan "Playlist habis".

**Root cause:** Mode presisi memuat video ke player per satu berkas (`loadfile` tunggal), sehingga player internal tidak mengetahui daftar video mendatang.

**Solusi:** `PrecisionScheduler.timeline()` sekarang menyusun daftar *Sudah Diputar*, *Sedang Diputar*, dan *Berikutnya* langsung dari data jadwal harian di memori, lalu menyediakannya ke frontend melalui endpoint `/api/status`.

---

### 4.11. Indikator counter restart player tidak sinkron (Terselesaikan)
**Solusi:** `precision.status()` mengekspos field `switch_count` dan `restart_every`. Frontend menghitung sisa pergantian video secara real-time dan menampilkannya pada status bar: `refresh player otomatis dalam N video (Mode Presisi)`.

---

### 4.12. Penanganan Layar Gelap (Blank) pada Slot Live CCTV, Jeda, dan File Hilang
**Gejala:** Ketika jadwal tiba pada slot `srt://...` (Live CCTV), jeda antarepisode, atau file video hilang, layar monitor/stream menjadi hitam pekat (blank) tanpa konten.

**Solusi yang diterapkan:**
1. **Fitur Video Pengganti (*Fallback Videos*):** Operator dapat memilih satu atau beberapa video pengisi melalui menu dashboard. Jika terjadi slot kosong atau file missing, scheduler otomatis memutar video dari daftar fallback secara berulang (*loop*) dan bergantian (*round-robin*).
2. **Relay Live CCTV Langsung:** Untuk baris `srt://...`, scheduler mengarahkan stream ke URL relay CCTV RTMP (`CCTV_RTMP`, default `rtmp://172.16.16.222:1935/live/livecctv`) jika tersedia.

---

### 4.13. Race Condition dan Restart FFmpeg Liar
**Gejala:** Pada backend FFmpeg, proses streaming sempat mengalami restart berulang-ulang dalam hitungan detik atau meninggalkan multiple child process di background.

**Solusi yang diterapkan:**
1. `FFmpegController` menerapkan `threading.Lock()` ketat di seluruh operasi proses (`_start_proc`, `_kill_proc`, `play`, `pause`, `stop`).
2. Proses lama dipastikan telah berhenti sempurna (`terminate` disusul `wait`) sebelum proses baru di-spawn.
3. Penambahan parameter buffer delay output (`VIDEO_DELAY=3`) untuk memastikan muxer FLV/RTMP memiliki waktu handshake yang cukup sebelum data dikirim.
4. Pencatatan log aktivitas proses secara transparan ke file `ffmpeg_log.txt` dan `scheduler_log.txt`.

---

### 4.14. Melesetnya Tanggal Siaran pada Jadwal Lintas Tengah Malam
**Gejala:** Program siaran yang berjalan melewati tengah malam (misal pukul 00:00 hingga 03:00 dini hari) salah membaca tanggal jadwal dan melompat ke jadwal hari berikutnya.

**Solusi yang diterapkan:**
Diimplementasikan fungsi `_broadcast_start_hour()` dan `_broadcast_date_and_seconds()` pada `precision_scheduler.py`:
- Sistem secara cerdas mendeteksi jam anchor siaran terawal dari rundown aktif (misal jam 06:00 pagi).
- Waktu antara pukul 00:00 hingga 05:59 WIB secara otomatis tetap dihitung sebagai bagian dari hari siaran sebelumnya (hari kemarin) dengan perhitungan timecode continuous (`>= 24:00:00`).

---

## 5. Fitur Jadwal Presisi & Alur Operasional

### 5.1. Cara Kerja Engine Presisi
1. Scheduler membaca jadwal hari ini berdasarkan perhitungan jam anchor siaran WIB.
2. Setiap `TICK_SECONDS` (2 detik), scheduler membandingkan waktu sekarang dengan rentang `start` dan `end` setiap entri jadwal.
3. Saat entri baru aktif:
   - Jika tipe `video`: Memuat file dan langsung seek ke `now - start_time`.
   - Jika tipe `live`: Memuat relay stream Live CCTV.
   - Jika tipe `missing` atau jeda (*gap*): Memuat video pengganti (*fallback*).
4. Setiap pergantian video, `switch_count` bertambah. Begitu mencapai target `chunk_size`, proses player di-restart untuk membersihkan RAM dan langsung disinkronkan kembali pada tick berikutnya.

### 5.2. Format & Cara Impor Jadwal
- Buka dashboard di browser, temukan panel **Playlist Tersimpan**.
- Masukkan file `.zip` (berisi kumpulan file `.playlist` harian) atau langsung file `.playlist`/`.ply`/`.txt`.
- Klik **Impor Playlist Harian**.
- Nama file (misal `2026-09-29.playlist` atau `2026_09_29.ply`) otomatis menjadi kunci tanggal jadwal.

### 5.3. Fitur Modal Rundown Siaran
- Di samping nama playlist tersimpan, klik tombol **👁 Rundown**.
- Muncul modal tabel rundown siaran:
  - Jam tayang WIB (format `HH:MM:SS`)
  - Judul program acara
  - Durasi program
  - Badge status:
    - 🟢 **Siap**: File video terverifikasi ada di folder video.
    - 🔴 **Missing**: File tidak ditemukan di storage (akan diputar fallback).
    - 📡 **Live**: Segmen siaran langsung Live CCTV.
  - Ringkasan total durasi siaran dan jumlah acara.

### 5.4. Pengaturan Video Pengganti (*Fallback Videos*)
- Pada panel **📺 Video Pengganti**, klik **+ Tambah Video Pengganti**.
- Navigasi pustaka video pada modal picker dan klik **Pilih**.
- Daftar video fallback tersimpan di `fallback_videos.json`.

---

## 6. File-file Lama (Legacy) yang Tersimpan di Pi

Di direktori home `/home/eksan/`, terdapat beberapa file arsip era lama sebelum dashboard web dibuat:

| File | Keterangan |
|---|---|
| `auto_player.sh`, `auto_player_nohwdec.sh` | Script bash lama untuk memutar playlist looping sederhana. Sudah tidak dipakai. |
| `playlist_ply.sh` | Script bash lama pembaca file `.ply`. Fungsinya telah digantikan penuh oleh `precision_scheduler.py`. |
| `convert_high_fps.sh` | Utilitas insidental untuk konversi frame rate video via FFmpeg. |

*Rekomendasi:* Simpan file-file ini hanya sebagai arsip referensi. Jangan menjalankan script-script ini bersamaan dengan service dashboard karena akan berebut hardware display/koneksi socket.

---

## 7. Cara Melakukan Update Kode

Untuk memperbarui kode di Raspberry Pi melalui komputer lokal/laptop:

```bash
# 1. Dari komputer pengembang, kirim arsip zip proyek ke Pi
scp -P 8041 raspi-dashboard.zip eksan@103.131.105.122:~/

# 2. Di terminal SSH Raspberry Pi:
sudo systemctl stop raspi-dashboard
unzip -o ~/raspi-dashboard.zip -d /home/eksan/raspi-dashboard
sudo systemctl start raspi-dashboard
sudo systemctl status raspi-dashboard
```

**Melihat log sistem secara langsung:**
```bash
sudo journalctl -u raspi-dashboard -f
```

---

## 8. Rekomendasi untuk Tim Pengembang Selanjutnya

1. **Ganti Password Dashboard & Kunci Akses:** Segera perbarui `DASHBOARD_PASSWORD_HASH` dan `DASHBOARD_SECRET_KEY` sebelum digunakan di lingkungan produksi penuh.
2. **Evaluasi Migrasi Penuh ke FFmpeg Headless:**
   - Pertimbangkan untuk beralih sepenuhnya ke backend FFmpeg (`DASHBOARD_PLAYER=ffmpeg`).
   - Dengan FFmpeg headless, sistem tidak lagi bergantung pada kabel HDMI fisik, port micro-HDMI Pi yang rapuh, ataupun unit hardware encoder eksternal di IP `172.16.100.225`.
   - Gunakan encoder hardware `h264_v4l2m2m` pada Pi untuk efisiensi CPU dan pantau suhu SoC (`vcgencmd measure_temp`).
3. **Backup Konfigurasi Systemd:** Selalu backup file `/etc/systemd/system/raspi-dashboard.service` sebelum melakukan deploy atau update besar.
4. **Isolasi Jaringan & Dependensi Pip:** Akses internet di Raspberry Pi ini dibatasi. Paket dependensi baru sebaiknya diunduh dalam bentuk wheel file (`.whl`) di komputer lokal lalu ditransfer manual via `scp`.
5. **Keamanan URL RTMP:** Jangan pernah menulis stream key atau URL RTMP produksi secara hardcoded di dalam source code yang tercommit ke Git publik. Manfaatkan environment variables di systemd.

---

## 9. Catatan Operasional: Troubleshooting Stream Live CCTV (VLC)

Panduan praktis jika menerima laporan stream siaran atau CCTV bermasalah saat ditonton melalui pemutar VLC penonton:

**Diagnosis Cepat dari VLC:** Tekan **Tools → Codec Information (`Ctrl+J`)**:
- Jika tab Codec **kosong total**: VLC gagal terhubung ke server streaming (masalah jaringan, port tertutup, atau publisher mati). Uji dari command line: `ffprobe <url-rtmp>`.
- Jika tab Codec **muncul normal** (misal `H264 - MPEG-4 AVC`, `MPEG AAC Audio`): Koneksi dan format stream sehat, kendala ada pada setting player penonton.

| Gejala | Kemungkinan Penyebab | Tindakan Solusi |
|---|---|---|
| Layar hitam / berkedip error berulang, namun suara normal | Frame video drop akibat fluktuasi jaringan | Naikkan nilai **Network Caching** di VLC: Tools → Preferences → All → Input/Codecs, ubah dari 300ms ke 1500–3000ms |
| Codec Info kosong total | Stream publisher mati atau server RTMP tidak terjangkau | Cek apakah service `raspi-dashboard` aktif dan apakah server RTMP tujuan sedang online |
| Gambar ada, audio bisu | Track audio ter-mute atau device output salah di penonton | Periksa Audio → Audio Track (pastikan aktif), cek Volume Mixer sistem operasi penonton |
| Audio dan gambar tidak sinkron (lipsync delay) | Latensi buffer streaming berbeda antara audio dan video | Tekan tombol shortcut keyboard **J** (mundur 50ms) atau **K** (maju 50ms) di VLC untuk menyelaraskan audio sambil menonton |
