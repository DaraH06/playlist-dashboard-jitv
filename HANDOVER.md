# Dokumentasi Handover — Sistem Playout Video JITV (Raspberry Pi)

Dokumen ini dibuat untuk serah terima proyek ke tim baru. Isinya mencakup
arsitektur sistem, infrastruktur, penjelasan source code, dan yang
terpenting — **riwayat masalah yang pernah terjadi beserta solusinya**,
supaya tim baru tidak perlu mengulang proses debugging yang sudah
dilakukan.

> **Update terbaru (10 September 2026):** 4 bug diperbaiki di sesi
> paling akhir — jadwal meleset jam karena timezone sistem (4.8),
> Daftar Jadwal menimpa Mode Presisi (4.9), panel "Berikutnya" selalu
> kosong di Mode Presisi (4.10), dan fitur impor diperluas dari
> "cuma .zip" jadi terima `.playlist`/`.ply`/`.txt` langsung (5.2).
> Ditambahkan juga catatan operasional troubleshooting stream CCTV di
> VLC (bagian 9). Lompat ke bagian-bagian itu kalau cuma mau baca
> yang baru.

---

## 1. Ringkasan Proyek

Sistem ini berfungsi memutar playlist video secara otomatis di
Raspberry Pi 4, lalu output video tersebut disiarkan (streaming) lewat
RTMP ke server, sehingga bisa ditonton dari VLC atau media player lain.

**Alur singkatnya:**
```
Raspberry Pi 4 (mutar video, output HDMI)
        ↓ kabel HDMI (micro-HDMI ke HDMI biasa)
Encoder H.264 (menangkap sinyal HDMI, ubah jadi stream RTMP)
        ↓ jaringan (RTMP)
Server tujuan RTMP
        ↓
VLC / media player penonton
```

Dulu playlist diputar pakai script bash manual (`auto_player.sh`),
sekarang sudah digantikan dengan **dashboard web** yang lebih mudah
dipakai — bisa susun playlist, kontrol play/pause/stop, dan atur jadwal
otomatis lewat browser.

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

**Kenapa IP-nya dibuat statis:** dulu IP Pi ini dapat otomatis dari
DHCP dan bisa berubah tiap restart — ini pernah bikin semua script
gagal akses video karena path/IP yang di-hardcode jadi tidak valid.
Sekarang sudah dikunci manual pakai `nmcli` supaya selalu sama.

### 2.2. Sumber Video (Network Share / Samba)
| Item | Nilai |
|---|---|
| Server | `10.200.253.14` |
| Nama share | `data_jitv1` |
| Mount point di Pi | `/mnt/videoserver` |
| Metode mount | CIFS/SMB, via `/etc/fstab` + `systemd automount` |
| File kredensial | `/home/eksan/.smbcredentials` (permission `600`, hanya bisa dibaca user `eksan`) |

**Riwayat penting:** server video ini **pernah pindah IP** dari
`172.16.100.161` ke `10.200.253.14` (migrasi ke server baru/datacenter).
Kalau video tiba-tiba tidak bisa diakses (`Cannot open file`), cek
dulu apakah server ini pindah IP lagi:
```bash
mount | grep videoserver
ping 10.200.253.14
```

### 2.3. Encoder (HDMI ke RTMP)
| Item | Nilai |
|---|---|
| IP | `172.16.100.225` |
| Akses | Browser ke `http://172.16.100.225`, login admin encoder |
| Fungsi | Menangkap sinyal HDMI dari Pi, kirim sebagai stream RTMP |

Di halaman admin encoder, menu **Encoder → Main stream**, ada kolom
**RTMP PUBLISH URL** — ini alamat tujuan stream (format:
`rtmp://user:pass@ip:port/app/stream_key`). Ada dropdown
**Enable/Disable** di sebelahnya untuk menyalakan/mematikan publish.

### 2.4. Dashboard Web (aplikasi utama untuk kontrol playlist)
| Item | Nilai |
|---|---|
| Lokasi di Pi | `/home/eksan/raspi-dashboard/` |
| Port | `5000` |
| Dikelola oleh | `systemd` service `raspi-dashboard.service` (auto-start saat Pi nyala) |
| Login | Username `admin`, password **perlu ditanyakan/di-reset oleh tim baru** (lihat bagian keamanan di bawah) |

**Cara akses dashboard dari luar jaringan kantor** (karena port 5000
tidak terbuka langsung ke internet), pakai SSH tunnel dulu:
```bash
ssh -p 8041 -L 5000:localhost:5000 eksan@103.131.105.122
```
Biarkan terminal itu tetap terbuka, lalu buka browser ke:
```
http://localhost:5000
```

**Rekomendasi keamanan untuk tim baru:** ganti password admin
dashboard begitu serah terima selesai (lihat langkah di README.md
bagian "Set username & password"), supaya tim lama tidak lagi punya
akses.

---

## 3. Penjelasan Source Code

Semua kode ada di `/home/eksan/raspi-dashboard/`.

| File | Fungsi |
|---|---|
| `app.py` | Flask app — mengatur semua route/URL (login, dashboard, API play/pause/stop/dll) |
| `mpv_controller.py` | "Otak" pengontrol mpv — start/stop video lewat IPC socket, plus mekanisme auto-restart berkala (jelasnya di bagian 4) |
| `scheduler.py` | **Jadwal Sederhana** — tiap 15 detik cek apakah ada jadwal manual yang waktunya tiba, lalu terapkan playlist. Otomatis diam kalau Mode Jadwal Presisi lagi aktif (lihat 4.9). |
| `precision_scheduler.py` | **Mode Jadwal Presisi** — mesin terpisah yang mutar video PERSIS sesuai timecode dari file `.playlist`/`.ply` yang diimpor (lihat bagian 5.2). |
| `templates/dashboard.html`, `templates/login.html` | Tampilan HTML halaman dashboard & login |
| `static/app.js` | Logic JavaScript — polling status, render playlist, kontrol tombol, dll |
| `static/style.css` | Styling tampilan (dark theme) |
| `playlist.json` | Playlist draft yang terakhir disusun di panel "Playlist Baru" |
| `named_playlists.json`* | Playlist-playlist yang disimpan dengan nama, dipakai untuk Jadwal Sederhana |
| `schedule.json`* | Daftar entri Jadwal Sederhana yang sudah dibuat |
| `precision_playlists.json`* | Jadwal presisi per tanggal (hasil impor `.playlist`/`.ply`/`.zip`/`.txt`), dipakai Mode Jadwal Presisi |
| `settings.json`* | Menyimpan setting "refresh player tiap N video" |
| `raspi-dashboard.service` | File systemd untuk menjalankan dashboard otomatis saat boot |

*File-file ini dibuat otomatis oleh aplikasi (tidak perlu dibuat manual),
tersimpan di folder yang sama.

### 3.1. Cara kerja pemutaran video (inti dari `mpv_controller.py`)
1. mpv dijalankan sekali dalam mode `--idle=yes` dengan IPC socket
   (`/tmp/mpvsocket`) — dashboard mengontrolnya lewat socket ini
   (kirim perintah play/pause/stop/dll), **bukan** dengan
   kill-and-restart proses tiap kali tombol ditekan (beda dari
   pendekatan script lama).
2. Playlist dikirim ke mpv lewat file `.m3u` sementara
   (`/tmp/dashboard_playlist.m3u`).

---

## 4. Riwayat Masalah & Solusi (PALING PENTING — baca ini)

### 4.1. Bug hardware decoder Raspberry Pi (memory leak `v4l2m2m`)
**Gejala:** RAM terus naik selama playlist diputar, sampai akhirnya
Raspberry Pi kehabisan memori dan hang total (perlu cabut-colok
power untuk pulih).

**Root cause (sudah dikonfirmasi lewat pengukuran RAM langsung):**
chip decoder video hardware di Raspberry Pi (`v4l2m2m`) **tidak
melepas memori sepenuhnya** setiap kali video berganti dalam satu
proses mpv yang sama. RAM naik terus (pola "tangga naik"), tidak
pernah turun balik.

**Solusi yang diterapkan:** proses mpv di-restart secara berkala
(bukan tiap video, karena itu bikin jeda terlalu sering) — jumlah
video sebelum restart bisa diatur dari dashboard (kolom "Refresh
player otomatis tiap N video", default 20, bisa 5-100). Saat restart,
posisi playlist tetap disimpan sehingga terasa seperti tidak ada
gangguan (cuma kedip hitam sepersekian detik).

**Bug turunan yang pernah terjadi:** mekanisme restart ini sempat
**tidak berfungsi** karena ada bug di kode — background thread yang
tugasnya restart mpv (`_ensure_watcher()`) **tidak pernah dipanggil**
kalau mpv sudah terlanjur jalan sebelum dashboard di-restart (misal
setelah `systemctl restart raspi-dashboard`). Ini menyebabkan RAM
mpv sempat menumpuk sampai 1.8GB tanpa pernah di-reset. **Bug ini
sudah diperbaiki** (lihat komentar di `mpv_controller.py` fungsi
`start()`), tapi kalau suatu saat gejala RAM menumpuk muncul lagi,
ini titik pertama yang harus dicek.

**Cara cek gejala ini muncul lagi:**
```bash
ps aux | grep mpv
```
Kalau kolom `RES` (RAM terpakai) sudah di atas 500MB-1GB dan terus
naik, kemungkinan mekanisme restart tidak berfungsi. Solusi darurat:
```bash
sudo pkill -9 -x mpv
sudo systemctl restart raspi-dashboard
```

### 4.2. Lag video karena fitur durasi video (`ffprobe`)
**Gejala:** video jadi patah-patah/lag padahal sebelumnya lancar,
tepat setelah fitur "tampilkan durasi tiap video di playlist"
ditambahkan.

**Root cause:** untuk tahu durasi video yang **belum** diputar,
sistem sempat memakai `ffprobe` untuk membaca metadata tiap file
lewat network share (SMB) — proses baca ini **berebut bandwidth
network** dengan mpv yang sedang streaming video dari server yang
sama, sehingga video jadi tersendat.

**Solusi yang diterapkan:** fitur durasi untuk video yang **belum**
diputar **dihapus total** (tidak ada `ffprobe` sama sekali sekarang).
Durasi cuma ditampilkan untuk video yang **sedang** diputar, karena
mpv sudah tahu durasi file itu dari dirinya sendiri (tidak perlu
`ffprobe`/network tambahan). **Jangan tambahkan lagi fitur durasi
untuk video yang belum diputar** kecuali dengan pendekatan berbeda
yang tidak membebani network share saat playback aktif.

### 4.3. IP server/perangkat berubah-ubah (DHCP)
**Gejala:** script/mount tiba-tiba error "no such file/device" atau
SSH/ping ke suatu IP tiba-tiba gagal total.

**Root cause:** beberapa perangkat (Pi, server video lama) awalnya
pakai DHCP, yang bisa kasih IP berbeda tiap kali perangkat itu
restart.

**Solusi:** Raspberry Pi sudah di-set statis (lihat bagian 2.1). Kalau
ada perangkat lain yang mengalami masalah serupa, cek dulu apakah
IP-nya berubah dengan `nmap -sn <subnet>/24` atau cek DHCP client
list di router.

### 4.4. Video di VLC tetap muter walau Pi sudah "Stop"
**Gejala:** klik Stop di dashboard, mpv di Pi sudah berhenti (bisa
dikonfirmasi lewat status dashboard), tapi VLC/penonton di ujung
RTMP **masih terus muter video yang sama**.

**Root cause:** server RTMP di tengah menyimpan buffer (GOP cache),
jadi walau sumber sudah berhenti kirim data baru, video "lama" yang
sempat ke-buffer tetap disuguhkan ke penonton.

**Solusi:** untuk benar-benar memutus stream, harus **disable publish
dari sisi encoder** (bagian 2.3: RTMP PUBLISH URL → Disable → Apply),
bukan cuma stop dari dashboard Pi. Kalau encoder tidak bisa diakses,
alternatif darurat: matikan sinyal HDMI dari Pi langsung:
```bash
vcgencmd display_power 0   # matikan
vcgencmd display_power 1   # nyalakan lagi nanti
```

### 4.5. Password dengan karakter `$` gagal ke-export di bash
**Gejala:** waktu setting password dashboard manual lewat
`export DASHBOARD_PASSWORD_HASH="..."`, sebagian hash hilang/terpotong.

**Root cause:** hash password (format `scrypt:...` atau `pbkdf2:...`)
mengandung karakter `$`, dan bash meng-interpretasikan `$` di dalam
**kutip ganda** (`" "`) sebagai variabel, bukan teks biasa.

**Solusi:** selalu pakai **kutip tunggal** (`' '`) untuk value yang
mengandung `$`, contoh:
```bash
export DASHBOARD_PASSWORD_HASH='scrypt:32768:8:1$abc...'
```
Ini tidak relevan lagi kalau setting password lewat file
`raspi-dashboard.service` (format `Environment=KEY=value` di situ
tidak butuh kutip sama sekali).

### 4.6. Video ada gambar tapi TIDAK ADA SUARA di sisi encoder/VLC
**Gejala:** video jalan normal di dashboard, gambar sampai ke VLC lewat
encoder, tapi sama sekali tidak ada suara — padahal file videonya
sendiri punya audio (kalau diputar biasa di laptop, ada suaranya).

**Root cause (kombinasi 2 hal):**
1. **Command mpv di dashboard awalnya tidak set opsi audio device sama
   sekali** (`--ao`, `--audio-device`), beda dengan script lama
   `play_video.sh` yang eksplisit set `--ao=alsa
   --audio-device=alsa/plughw:1,0`. Ini sudah diperbaiki — command mpv
   di `mpv_controller.py` sekarang sudah menyertakan opsi ini.
2. **Yang jadi penyebab utama sebenarnya:** file `/boot/firmware/config.txt`
   di Pi **tidak punya baris `hdmi_drive`**. Tanpa ini, HDMI Pi bisa
   "salah negosiasi" jadi mode **DVI** (mode video-only, secara desain
   protokol **tidak bisa membawa audio sama sekali**, apapun setting
   ALSA/mpv di sisi software). Selain itu, encoder capture device juga
   bukan monitor asli — kadang tidak merespons "jabat tangan" EDID
   dengan normal, yang bisa membuat Pi mengira tidak ada apa-apa yang
   tersambung ke HDMI.

**Solusi:** tambahkan 2 baris ini di `/boot/firmware/config.txt`,
lalu **wajib reboot** (config ini cuma dibaca saat boot):
```
hdmi_force_hotplug=1
hdmi_drive=2
```
- `hdmi_drive=2` → paksa mode HDMI penuh (bawa audio), bukan DVI.
- `hdmi_force_hotplug=1` → paksa Pi tetap kirim sinyal HDMI walau
  encoder tidak merespons deteksi EDID seperti monitor normal.

**Catatan untuk tim baru:** kalau encoder atau Raspberry Pi diganti
unit baru ke depannya, cek dulu apakah 2 baris config ini sudah ada di
`config.txt` unit yang baru — kalau tidak, kemungkinan besar masalah
"gambar ada, suara tidak ada" ini akan muncul lagi.

### 4.7. Sistem playlist `.ply` (broadcast automation lama)
**UPDATE (lihat 5.2):** catatan lama di bawah ini sempat bilang
format `.ply`/timecode presisi ini "sudah tidak dipakai lagi". Itu
**sudah tidak berlaku** — dashboard sekarang justru punya **Mode
Jadwal Presisi** (`precision_scheduler.py`) yang membaca ulang format
timecode yang sama persis, jadi kemampuan "mutar video presis sesuai
jam" dari sistem lama itu **sudah dihidupkan kembali**, hanya lewat
mesin baru, bukan software broadcast automation yang lama. Script
`playlist_ply.sh` yang disebut di bawah tetap tidak dipakai (lihat
bagian 6) — fungsinya sudah digantikan `precision_scheduler.py`.

Sebelum dashboard ini dibuat, ada folder `Daily Playlist/` di server
video berisi file `.ply` (satu file per hari, format khusus software
broadcast automation lama) yang berisi daftar video + jadwal jam
tayang presisi. Software yang generate file ini sudah berhenti sejak
sekitar April 2026 — file terakhir yang ada tanggal `2026_04_02`.
Kalau tim baru masih punya sumber file `.playlist`/`.ply` harian dari
sistem lain (mis. dari software broadcast automation yang berbeda),
file itu tetap bisa diimpor manual lewat dashboard (bagian 5.2).

### 4.8. Jadwal (Sederhana maupun Presisi) meleset jamnya
**Gejala:** video yang harusnya main jam sekian, mainnya di jam yang
salah — meleset konsisten (bukan acak).

**Root cause:** `scheduler.py` dan `precision_scheduler.py` awalnya
pakai `datetime.now()` polos di Python, yang ikut zona waktu **jam
sistem Raspberry Pi itu sendiri**. Kalau jam sistem Pi kebetulan tidak
di-set ke WIB (Raspberry Pi OS yang belum dikonfigurasi biasanya
default UTC), semua pencocokan jadwal otomatis geser sesuai selisih
zona waktu itu.

**Solusi:** kedua file itu sekarang eksplisit pakai
`zoneinfo.ZoneInfo("Asia/Jakarta")`, jadi jadwal jalan sesuai WIB
**tidak peduli** jam sistem Pi-nya di-set ke zona apa. Tetap disarankan
jam sistem Pi di-set benar (`timedatectl`) sebagai kebersihan umum,
tapi jadwal tidak lagi bergantung padanya.

### 4.9. Daftar Jadwal (Sederhana) menimpa Mode Jadwal Presisi
**Gejala:** Mode Jadwal Presisi sedang aktif dan berjalan benar, tapi
tiba-tiba video "lompat" balik ke playlist lain dari awal, tidak
sesuai timecode presisi yang seharusnya.

**Root cause:** Daftar Jadwal (Sederhana) dan Mode Jadwal Presisi
sama-sama mengendalikan mpv yang sama, tapi dulu **tidak saling tahu**
satu sama lain. Kalau ada entri di Daftar Jadwal yang jatuh tempo
(misalnya sisa entri lama yang lupa dihapus) sementara Mode Presisi
lagi aktif, entri itu tetap nembak `load_playlist()` ke mpv dan
menimpa apapun yang sedang diatur Mode Presisi. Mode Presisi sendiri
tidak proaktif mengoreksi balik — dia cuma bertindak saat entri
jadwalnya sendiri berganti.

**Solusi:** `scheduler.py` sekarang menerima referensi status Mode
Presisi (`is_precision_active`) dari `app.py`. Kalau Mode Presisi
sedang aktif, entri Daftar Jadwal yang jatuh tempo **dilewati**
(dianggap "sudah jalan" hari itu supaya tidak nyusul nembak nanti,
tapi tidak benar-benar mengeksekusi). Tetap disarankan: hapus entri
Daftar Jadwal yang sudah tidak relevan supaya tidak membingungkan,
walau sekarang secara teknis sudah aman.

### 4.10. Panel "Yang Sedang Berjalan di Player" selalu bilang playlist habis di Mode Presisi
**Gejala:** kolom "Sudah Diputar" dan "Berikutnya" di panel "Yang
Sedang Berjalan di Player" selalu kosong ("Playlist sudah habis")
walau Mode Presisi aktif dan jadwal hari itu masih panjang. Padahal
teks ringkasan di atas panel Mode Presisi ("Sekarang" / "Berikutnya")
sudah benar.

**Root cause:** panel itu aslinya baca playlist bawaan **mpv sendiri**
(`mpv.status()`). Tapi Mode Presisi tidak pernah memuat playlist penuh
ke mpv — dia memuat **satu file per satu file** lewat `loadfile ...
replace` (lihat `load_file_and_seek` di `mpv_controller.py`), supaya
bisa seek ke posisi yang tepat sesuai jam. Akibatnya di mata mpv,
"playlist"-nya memang selalu cuma berisi 1 video, jadi tidak pernah
ada "berikutnya" yang bisa dibaca dari sana.

**Solusi:** ditambahkan `PrecisionScheduler.timeline()` yang
menurunkan daftar sudah-diputar/sedang-diputar/berikutnya langsung
dari jadwal presisi hari itu (bukan dari mpv), dikirim lewat
`/api/status` sebagai field `precision_timeline`, dan dipakai
`static/app.js` untuk isi panel itu kalau Mode Presisi aktif.

### 4.11. Counter restart Mode Presisi tidak kelihatan di dashboard
**Gejala:** teks "refresh player otomatis dalam N video" di atas
panel kontrol player tidak pernah berkurang selama Mode Presisi
aktif — jadi tidak bisa dipakai buat mastiin apakah mekanisme
anti-leak (4.1) di Mode Presisi beneran jalan atau tidak, terutama
kalau RAM proses mpv (cek `htop`, kolom RES) kelihatan sudah naik
tinggi (500MB-1GB+).

**Root cause:** teks itu baca `chunk_progress` milik mode Sederhana,
yang **sengaja** berhenti dihitung selama Mode Presisi aktif (lihat
komentar `precision_mode_active` di `mpv_controller.py`) — Mode
Presisi punya counter sendiri (`_switch_count` di
`precision_scheduler.py`), tapi sebelumnya tidak diekspos ke API/UI
sama sekali.

**Solusi:** `precision.status()` sekarang menyertakan `switch_count`
dan `restart_every`, diteruskan lewat `/api/status` sebagai
`precision_switch_count`/`precision_restart_every`, dan teks di
dashboard otomatis pakai angka ini kalau Mode Presisi aktif. Kalau
suatu saat mpv RES tetap naik terus **melewati** angka restart yang
ditampilkan (bukan cuma tinggi sesaat sebelum restart), berarti
`restart_process_only()` gagal beneran mematikan proses lama — cek
dengan `ps aux | grep mpv` apakah ada lebih dari satu proses mpv
nyangkut bareng.

---

## 5. Fitur Jadwal Otomatis

Dashboard sekarang punya **dua** mekanisme jadwal terpisah, yang tidak
boleh dianggap sama:

### 5.1. Jadwal Sederhana ("Daftar Jadwal")
Cocok untuk kebutuhan "mainkan playlist ini setiap jam segini", tidak
presisi sampai hitungan detik:

1. **Playlist Bernama** — playlist yang disimpan dengan nama khusus
   (terpisah dari playlist draft biasa), dipakai sebagai referensi
   di jadwal.
2. **Jadwal** — tiap entri jadwal berisi: jam mulai, jenis
   pengulangan (setiap hari / hari tertentu / tanggal spesifik), dan
   playlist bernama mana yang mau dimainkan.
3. Background thread di `scheduler.py` mengecek tiap 15 detik, dan
   otomatis menerapkan playlist begitu jamnya tiba (memuat dari video
   pertama, bukan seek presisi). Jadwal tanggal spesifik mengalahkan
   jadwal berulang di hari yang sama.
4. **Otomatis diam kalau Mode Jadwal Presisi (5.2) sedang aktif** —
   lihat 4.9.

Detail cara pakai lengkap ada di `README.md` bagian "Jadwal otomatis".

### 5.2. Mode Jadwal Presisi
Mesin terpisah (`precision_scheduler.py`) yang mutar video **PERSIS**
sesuai timecode dari file `.playlist`/`.ply` broadcast automation
(lihat 4.7) — kalau baru diaktifkan di tengah jam tayang suatu video,
otomatis seek langsung ke posisi yang seharusnya, bukan mulai dari
0:00.

**Cara impor jadwal:**
- Terima file `.zip` (isi banyak `.playlist`, 1 file per tanggal), ATAU
  file `.playlist`/`.ply`/`.txt` mentah langsung (satu atau banyak
  sekaligus) — tidak perlu di-zip dulu.
- Baris `srt://...` (segmen live CCTV) dan baris penanda acara teks
  (mis. "STOP CCTV SIANG") otomatis dilewati/tidak dianggap video.
- Nama file (tanpa ekstensi) jadi nama tanggal jadwal, format
  `YYYY_MM_DD` diubah otomatis jadi `YYYY-MM-DD`.

**Keterbatasan yang disengaja (bukan bug):**
- **Segmen live CCTV belum didukung** — kalau timecode-nya jatuh ke
  segmen `srt://...`, yang tampil cuma layar hitam ("blank"), sama
  seperti kalau file videonya hilang/tidak ketemu. Kalau ke depannya
  perlu benar-benar nyalain live CCTV di jam itu, itu fitur baru yang
  belum ada.
- Restart berkala mpv (mitigasi memory leak, lihat 4.1) tetap berlaku
  di Mode Presisi, dengan mekanisme terpisah dari Jadwal Sederhana
  (lihat komentar di `precision_scheduler.py`).

---

## 6. File-file Lama (Legacy) yang Masih Tersimpan di Pi

Di `/home/eksan/`, masih ada beberapa script dari sebelum dashboard
dibuat. **Ini disimpan sebagai cadangan/riwayat, bukan yang aktif
dipakai sekarang:**

| File | Riwayat |
|---|---|
| `auto_player.sh`, `auto_player_nohwdec.sh` | Script bash awal untuk mutar playlist looping (sebelum ada dashboard). Sudah digantikan dashboard. |
| `playlist_ply.sh` | Baca file `.ply` broadcast automation lama (lihat 4.6). Sudah tidak relevan karena sumbernya berhenti update. |
| `play_video.sh` | Dipakai bareng sistem `scheduled_ipc_player.py` (sudah dihapus, percobaan lama pribadi eksan, sudah tidak dipakai). |
| `convert_high_fps.sh` | Utility buat convert frame rate video pakai ffmpeg, dipakai insidental. |

**Rekomendasi:** boleh dihapus kalau tim baru yakin dashboard sudah
jadi satu-satunya cara mutar playlist ke depannya. Simpan dulu sebagai
arsip/referensi kalau masih ragu.

---

## 7. Cara Update Kode Dashboard

Karena `pip`/`npm` di Pi ini dibatasi akses network-nya, cara paling
praktis update kode adalah transfer file lewat `scp` dari komputer
yang sudah punya kode terbaru:

```bash
# Dari komputer/laptop, kirim file zip project ke Pi
scp -P 8041 raspi-dashboard.zip eksan@103.131.105.122:~/

# Di Pi:
sudo systemctl stop raspi-dashboard
unzip -o raspi-dashboard.zip -d raspi-dashboard
sudo systemctl start raspi-dashboard
sudo systemctl status raspi-dashboard   # pastikan "active (running)"
```

**Cek log kalau ada masalah:**
```bash
sudo journalctl -u raspi-dashboard -n 50 --no-pager
```

---

## 8. Rekomendasi untuk Tim Selanjutnya

1. **Ganti password dashboard** (`admin`/`admin` saat ini) — lihat
   README.md bagian "Set username & password".
2. **Rotasi kredensial Samba** (`/home/eksan/.smbcredentials`) kalau
   memang perlu sebagai bagian dari serah terima akses.
3. **Backup `raspi-dashboard.service`** dari `/etc/systemd/system/`
   sebelum melakukan perubahan besar — ini file konfigurasi env var
   penting yang tidak ikut ter-backup otomatis kalau cuma
   backup folder `raspi-dashboard/`.
4. Kalau ke depannya butuh **presisi jadwal sampai hitungan detik**
   (seperti sistem `.ply` lama), fitur jadwal dashboard yang ada
   sekarang **belum dirancang untuk itu** — perlu pengembangan
   tambahan kalau requirement-nya berubah ke arah situ.
5. Pertimbangkan migrasi dari Flask development server (`app.run()`)
   ke **production WSGI server** (misal `gunicorn`) kalau dashboard
   ini akan dipakai jangka panjang — saat ini masih pakai server
   development bawaan Flask yang aslinya tidak didesain untuk
   penggunaan produksi (ada peringatan soal ini di log setiap kali
   dashboard start).
6. **Jangan asal nambah dependency baru ke `requirements.txt`** —
   `pip` di Pi ini dibatasi akses network-nya (lihat bagian 7). Sempat
   ada percobaan nambah paket `tzdata` untuk jaga-jaga `zoneinfo`
   (lihat 4.8), tapi dilepas lagi karena Raspberry Pi OS biasanya
   sudah punya database zona waktu sistem sendiri
   (`/usr/share/zoneinfo`) — jadi `zoneinfo` jalan tanpa paket pip
   tambahan. Kalau suatu saat butuh dependency baru yang benar-benar
   perlu ditarik dari PyPI, siapkan dulu cara transfer manual (`pip
   download` di komputer lain lalu `scp` file `.whl`-nya), jangan
   asumsikan Pi bisa akses internet langsung.

---

## 9. Catatan Operasional: Troubleshooting Stream Live CCTV (VLC)

Ini bukan bug di kode dashboard, tapi catatan hasil debugging langsung
lewat VLC waktu ngecek salah satu stream RTMP CCTV
(`rtmp://103.255.15.222:1935/live/demo`) — berguna kalau ke depannya
ada laporan serupa dari stream CCTV lain (proyek `cctv-dashboard`,
~60 kamera se-DIY, terpisah dari dashboard playlist ini).

**Cara cepat diagnosis dari VLC:** Tools → Codec Information (`Ctrl+J`)
sambil stream lagi kebuka — kalau tab Codec-nya **kosong total**
(tidak ada info Muxer/Video/Audio sama sekali, field Location juga
kosong), itu tanda VLC gagal baca stream-nya sama sekali (biasanya
masalah jaringan/koneksi ke server RTMP, cek pakai `ffprobe <url>`
dari command line buat mastiin bukan cuma masalah di VLC). Kalau info
codec-nya muncul normal (mis. `H264 - MPEG-4 AVC`, `MPEG AAC Audio`),
berarti koneksi & format streamnya sehat — masalahnya ada di tempat
lain (lihat poin-poin di bawah).

| Gejala | Kemungkinan penyebab | Solusi |
|---|---|---|
| Layar hitam + teks log error berulang, padahal audio tetap jalan | Paket video drop di jaringan (bitstream H.264 butuh reference frame utuh; audio jauh lebih ringan jadi tetap lancar) | Naikkan **Network Caching** di Tools → Preferences → All → Input/Codecs, dari default ~300ms ke 1500-3000ms |
| Tab Codec Info di VLC kosong total | VLC gagal connect/baca stream-nya sama sekali | Cek `ffprobe rtmp://...` dari command line; kalau itu juga gagal, masalahnya di server/encoder RTMP-nya, bukan di VLC |
| Ada gambar, tidak ada suara sama sekali | Cek dulu apa streamnya memang punya audio track (Codec Info → ada baris "Stream 1: Audio"?). Kalau ada tapi tetap bisu, biasanya track audio ke-set "Disable" di menu Audio → Track, atau ke-mute, atau Audio Device salah, atau ke-mute di Volume Mixer Windows | Cek satu-satu: Audio → Track (harus di track-nya, bukan Disable) → Audio Device → tombol Mute di player → Volume Mixer Windows |
| Suara keluar tapi telat dari gambar (lip-sync meleset) | Wajar untuk live RTMP — video & audio kadang punya buffer/latency berbeda dikit, bisa geser-geser sendiri per sesi | Cara cepat: tombol keyboard **J**/**K** buat geser audio 50ms per tekan sambil nonton. Cara presisi: Tools → Track Synchronization → Audio track synchronization (isi ms, negatif kalau audio ketinggalan). Kalau abis naikin Network Caching delay-nya makin kerasa, coba turunin lagi ke 800-1000ms. |

**Catatan penting:** untuk kasus audio (baris ke-3 di tabel), ini
**beda root cause** dari masalah "gambar ada suara tidak ada" yang
sudah pernah terjadi di Raspberry Pi playout sendiri (lihat 4.6, soal
`hdmi_drive` di `config.txt`). Yang di bagian 9 ini soal nonton
stream-nya di VLC/PC penonton, bukan soal Pi yang mutar videonya.
