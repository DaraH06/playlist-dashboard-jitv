import logging
import os

class TruncatingFileHandler(logging.FileHandler):
    def __init__(self, filename, max_bytes, mode='a', encoding='utf-8', delay=False):
        super().__init__(filename, mode, encoding, delay)
        self.max_bytes = max_bytes

    def emit(self, record):
        if os.path.exists(self.baseFilename):
            if os.path.getsize(self.baseFilename) >= self.max_bytes:
                # Mengosongkan isi file log saat batas ukuran tercapai
                with open(self.baseFilename, "w") as f:
                    f.truncate(0)
        super().emit(record)


def setup_logger(name, log_file, max_bytes=5 * 1024 * 1024):
    """
    Helper untuk membuat logger satu file tunggal yang otomatis dibersihkan.
    - log_file: nama file log (ditentukan oleh pemanggil)
    - max_bytes: batas ukuran file sebelum dibersihkan (default: 5 MB)
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        # Nama file (log_file) dikirim langsung ke TruncatingFileHandler
        handler = TruncatingFileHandler(log_file, max_bytes=max_bytes)
        formatter = logging.Formatter('%(asctime)s: %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


scheduler_logger = setup_logger("scheduler", "log_scheduler.log")
ffmpeg_logger = setup_logger("ffmpeg", "log_ffmpeg.log")
mpv_logger = setup_logger("mpv", "log_mpv.log")
hasil_logger = setup_logger("video", "hasil.log")
hasil_logg = setup_logger("audio", "hasil2.log")