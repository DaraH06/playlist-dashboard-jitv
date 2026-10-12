from collections import Counter
import os
import sys


def parse_video_log(file_path):
  """Membaca file log video dan mengekstrak: codec, resolusi, framerate, pixel_format"""
  video_data = []
  with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
      cleaned = line.strip()
      parts = [p.strip() for p in cleaned.split(",")]
      # Format video dari ffprobe: codec, width, height, framerate, pix_fmt (total 5 bagian)
      if len(parts) >= 5:
        # Validasi bahwa lebar & tinggi adalah angka (misal: 1920, 1080)
        if parts[1].isdigit() and parts[2].isdigit():
          video_data.append({
              "codec": parts[0].lower(),
              "resolution": f"{parts[1]}x{parts[2]}",
              "framerate": parts[3],
              "pixel_format": parts[4].lower(),
          })
  return video_data


def parse_audio_log(file_path):
  """Membaca file log audio dan mengekstrak: codec, sample_rate, channels"""
  audio_data = []
  with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
      cleaned = line.strip()
      parts = [p.strip() for p in cleaned.split(",")]
      # Format audio dari ffprobe: codec, sample_rate, channels (total 3 bagian)
      if len(parts) >= 3:
        # Validasi bahwa sample_rate adalah angka (misal: 44100)
        if parts[1].isdigit():
          audio_data.append({
              "codec": parts[0].lower(),
              "sample_rate": f"{parts[1]} Hz",
              "channels": f"{parts[2]} channel(s)",
          })
  return audio_data


def main():
  import argparse

  parser = argparse.ArgumentParser(
      description=(
          "Skrip Rangkuman Atribut Video & Audio dari Log FFprobe Multi-baris"
      )
  )
  parser.add_argument(
      "-v",
      "--video-log",
      type=str,
      help="Path file teks log hasil scan video.",
  )
  parser.add_argument(
      "-a",
      "--audio-log",
      type=str,
      help="Path file teks log hasil scan audio.",
  )

  args = parser.parse_args()

  if not args.video_log and not args.audio_log:
    print(
        "❌ Error: Harap masukkan minimal salah satu argumen (--video-log atau"
        " --audio-log)."
    )
    print("Contoh: python analyze_media.py -v video_log.txt -a audio_log.txt")
    sys.exit(1)

  # --- PROSES VIDEO ---
  if args.video_log:
    if not os.path.exists(args.video_log):
      print(f"❌ File video log tidak ditemukan: {args.video_log}")
      sys.exit(1)

    video_data = parse_video_log(args.video_log)

    print("=" * 60)
    print(f"📊 RANGKUMAN VIDEO (Total Berhasil Dibaca: {len(video_data)} file)")
    print("=" * 60)
    if video_data:
      c_codec = Counter(x["codec"] for x in video_data)
      c_res = Counter(x["resolution"] for x in video_data)
      c_fps = Counter(x["framerate"] for x in video_data)
      c_pix = Counter(x["pixel_format"] for x in video_data)

      print(
          "• Codec       :",
          ", ".join([f"{k.upper()} ({v} file)" for k, v in c_codec.items()]),
      )
      print(
          "• Resolusi    :",
          ", ".join([f"{k} ({v} file)" for k, v in c_res.items()]),
      )
      print(
          "• Framerate   :",
          ", ".join([f"{k} fps ({v} file)" for k, v in c_fps.items()]),
      )
      print(
          "• Pixel Format:",
          ", ".join([f"{k} ({v} file)" for k, v in c_pix.items()]),
      )
    else:
      print("⚠️ Tidak ada data video valid yang terbaca.")
    print("=" * 60 + "\n")

  # --- PROSES AUDIO ---
  if args.audio_log:
    if not os.path.exists(args.audio_log):
      print(f"❌ File audio log tidak ditemukan: {args.audio_log}")
      sys.exit(1)

    audio_data = parse_audio_log(args.audio_log)

    print("=" * 60)
    print(f"📊 RANGKUMAN AUDIO (Total Berhasil Dibaca: {len(audio_data)} file)")
    print("=" * 60)
    if audio_data:
      a_codec = Counter(x["codec"] for x in audio_data)
      a_sr = Counter(x["sample_rate"] for x in audio_data)
      a_ch = Counter(x["channels"] for x in audio_data)

      print(
          "• Audio Codec :",
          ", ".join([f"{k.upper()} ({v} file)" for k, v in a_codec.items()]),
      )
      print(
          "• Sample Rate :",
          ", ".join([f"{k} ({v} file)" for k, v in a_sr.items()]),
      )
      print(
          "• Channels    :",
          ", ".join([f"{k} ({v} file)" for k, v in a_ch.items()]),
      )
    else:
      print("⚠️ Tidak ada data audio valid yang terbaca.")
    print("=" * 60)


if __name__ == "__main__":
  main()