"""Generate a precision .playlist file from a local video directory.

Requires ffprobe from an FFmpeg installation:
    python generate_playlist.py --video-dir D:\\TestVideo
"""
import argparse
import os
import subprocess
import sys
from datetime import date, datetime


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".ts", ".m4v")


def probe_video_duration(path, ffprobe="ffprobe"):
    result = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "ffprobe gagal membaca file"
        raise RuntimeError(f"{path}: {detail}")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"{path}: durasi ffprobe tidak valid") from exc
    if duration <= 0:
        raise RuntimeError(f"{path}: durasi harus lebih besar dari nol")
    return duration


def seconds_to_timecode(seconds, fps=25):
    total_frames = int(round(seconds * fps))
    total_seconds, frame = divmod(total_frames, fps)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"


def parse_start_time(value):
    try:
        parsed = datetime.strptime(value, "%H:%M:%S")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--start-time harus berformat HH:MM:SS"
        ) from exc
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def scan_videos(video_dir):
    videos = []
    for root, dirs, filenames in os.walk(video_dir):
        dirs.sort(key=str.lower)
        for filename in sorted(filenames, key=str.lower):
            if filename.lower().endswith(VIDEO_EXTENSIONS):
                full_path = os.path.join(root, filename)
                relative = os.path.relpath(full_path, video_dir)
                videos.append((full_path, relative.replace(os.sep, "/")))
    return videos


def build_playlist(video_dir, start_seconds, fps, ffprobe):
    videos = scan_videos(video_dir)
    if not videos:
        raise RuntimeError(f"Tidak ada video didukung di: {video_dir}")

    rows = []
    current = float(start_seconds)
    for full_path, relative_path in videos:
        duration = probe_video_duration(full_path, ffprobe)
        rows.append(
            (
                seconds_to_timecode(current, fps),
                seconds_to_timecode(duration, fps),
                f"UTF8{relative_path}",
            )
        )
        current += duration
    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Buat file .playlist presisi dari folder video lokal."
    )
    parser.add_argument("--video-dir", required=True, help="Folder root video")
    parser.add_argument(
        "--start-time",
        type=parse_start_time,
        default=6 * 3600,
        help="Jam mulai siaran, default 06:00:00",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Nama tanggal playlist YYYY-MM-DD",
    )
    parser.add_argument(
        "--output",
        help="Path output; default <date>.playlist di folder saat ini",
    )
    parser.add_argument("--fps", type=int, default=25, choices=(25, 30))
    parser.add_argument("--ffprobe", default="ffprobe", help="Path executable ffprobe")
    args = parser.parse_args()

    video_dir = os.path.abspath(args.video_dir)
    if not os.path.isdir(video_dir):
        parser.error(f"--video-dir bukan folder: {video_dir}")
    output = args.output or f"{args.date}.playlist"

    try:
        rows = build_playlist(video_dir, args.start_time, args.fps, args.ffprobe)
    except FileNotFoundError:
        print(
            "ffprobe tidak ditemukan. Install FFmpeg dan tambahkan folder bin ke PATH.",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    with open(output, "w", encoding="utf-8", newline="\n") as playlist:
        for start, duration, content in rows:
            playlist.write(f"{start}\t{duration}\t{content}\n")

    print(f"Playlist dibuat: {os.path.abspath(output)}")
    print(f"Jumlah video: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
