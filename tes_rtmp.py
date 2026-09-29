import socket
import time
from urllib.parse import urlparse

def validasi_koneksi_rtmp(rtmp_url, ukuran_tes_mb=9):
    """
    Menggabungkan pengecekan latensi (Ping) dan kecepatan koneksi (Bandwidth)
    ke server target sebelum menjalankan streaming.
    """
    parsed_url = urlparse(rtmp_url)
    host = parsed_url.hostname
    port = parsed_url.port if parsed_url.port else 1935
    
    if not host:
        print("Error: Format URL RTMP tidak valid.")
        return False

    print(f"=== Memulai Validasi Jaringan ke [{host}:{port}] ===")
    
    # ----------------------------------------------------
    # TAHAP 1: CEK LATENSI (TCP PING)
    # ----------------------------------------------------
    try:
        start_ping = time.perf_counter()
        s = socket.create_connection((host, port), timeout=3)
        end_ping = time.perf_counter()
        
        latensi = (end_ping - start_ping) * 1000
        print(f"🔹 Latensi Jaringan : {latensi:.2f} ms")
        
    except (socket.timeout, ConnectionRefusedError):
        print("Error: Server tidak merespons atau port 1935 tertutup.")
        return False
    except Exception as e:
        print(f"Error Koneksi: {e}")
        return False

    # ----------------------------------------------------
    # TAHAP 2: CEK KECEPATAN (BANDWIDTH TEST)
    # ----------------------------------------------------
    try:
        # Siapkan data tiruan di memori (contoh: 8 MB)
        data_uji = b"X" * (ukuran_tes_mb * 1024 * 1024)
        
        print(f"🔹 Menguji kecepatan dengan mengirim {ukuran_tes_mb} MB data...")
        start_speed = time.perf_counter()
        
        # Kirim data uji melalui socket yang sudah terbuka tadi
        s.sendall(data_uji)
        
        end_speed = time.perf_counter()
        s.close()  # Tutup koneksi setelah tes selesai
        
        durasi_kirim = end_speed - start_speed
        # Rumus konversi Byte ke Megabit per detik (Mbps)
        kecepatan_mbps = (ukuran_tes_mb * 8) / durasi_kirim
        
        print(f"🔹 Kecepatan Koneksi: {kecepatan_mbps:.2f} Mbps")
        
    except Exception as e:
        print(f"Peringatan (Tes Kecepatan Gagal): {e}")
        print("   Server tujuan mungkin langsung memutuskan koneksi setelah handshake.")
        s.close()
        return False

    # ----------------------------------------------------
    # TAHAP 3: KESIMPULAN KELAYAKAN
    # ----------------------------------------------------
    print("\n=== KESIMPULAN REKOMENDASI ===")
    if latensi < 10 and kecepatan_mbps > 50:
        print("KONDISI SEMPURNA: Sangat aman untuk bitrate tinggi 8.5 Mbps (8500k).")
        return True
    elif kecepatan_mbps > 15:
        print("ONDISI CUKUP: Bisa untuk 8.5 Mbps, tapi disarankan turunkan ke 4000k-5000k untuk cari aman.")
        return True
    else:
        print("KONDISI BURUK: Kecepatan terlalu rendah. Jangan gunakan bitrate di atas 2500k.")
        return False


url_server_sebelah = "rtmp://jitv:jitv@103.255.15.138:1935/live/demo"

koneksi_aman = validasi_koneksi_rtmp(url_server_sebelah)
