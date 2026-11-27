#!/usr/bin/env python3
"""Unit test for deferred output URL change in FFmpegController."""
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("RTMP_TARGET", "rtmp://test/stream")
os.environ.setdefault("MEDIA_PLAYER", "ffmpeg")

sys.path.insert(0, os.path.dirname(__file__))
from unittest.mock import Mock
import types

logger_stub = types.ModuleType("logger")
for logger_name in ("ffmpeg_logger", "ffmpeg_stats_srcdebug", "ffmpeg_stats_pubdebug", "mpv_logger"):
    setattr(logger_stub, logger_name, Mock())
sys.modules["logger"] = logger_stub

from ffmpeg_controller import FFmpegController


sys.path.insert(0, os.path.dirname(__file__))

from ffmpeg_controller import FFmpegController

def test_change_output_when_no_source():
    """Pending change should apply immediately when no source is running."""
    with patch('subprocess.Popen'):
        ctrl = FFmpegController(rtmp_url="rtmp://old/stream")
        ctrl._publisher_proc = MagicMock(poll=lambda: None)
        ctrl._source_proc = None
        ctrl._index = None

        ctrl.change_output("rtmp://new/stream")
        assert ctrl.rtmp_url == "rtmp://new/stream"
        assert ctrl._pending_rtmp_url is None
        print("✓ test_change_output_when_no_source")

def test_change_output_when_source_idle():
    """Pending change should apply immediately when source is not running."""
    with patch('subprocess.Popen'):
        ctrl = FFmpegController(rtmp_url="rtmp://old/stream")
        ctrl._publisher_proc = MagicMock(poll=lambda: None)
        ctrl._source_proc = MagicMock(poll=lambda: 0)  # dead
        ctrl._index = None
        ctrl._playlist = []

        ctrl.change_output("rtmp://new/stream")
        assert ctrl.rtmp_url == "rtmp://new/stream"
        assert ctrl._pending_rtmp_url is None
        print("✓ test_change_output_when_source_idle")

def test_change_output_when_file_playing():
    """File source: change should be pending until boundary."""
    with patch('subprocess.Popen'):
        ctrl = FFmpegController(rtmp_url="rtmp://old/stream")
        ctrl._publisher_proc = MagicMock(poll=lambda: None)
        src = MagicMock(poll=lambda: None)
        src.args = ["ffmpeg", "-i", "/path/video.mp4"]
        ctrl._source_proc = src
        ctrl._index = 0
        ctrl._playlist = ["/path/video.mp4"]
        ctrl._loop_file = False

        ctrl.change_output("rtmp://new/stream")
        assert ctrl.rtmp_url == "rtmp://old/stream"
        assert ctrl._pending_rtmp_url == "rtmp://new/stream"
        assert ctrl.output_change_pending() is True
        assert ctrl.output_change_waiting_for_boundary() is False
        print("✓ test_change_output_when_file_playing")

def test_change_output_when_live_playing():
    """Live source: change should stay pending until explicit boundary."""
    with patch('subprocess.Popen'):
        ctrl = FFmpegController(rtmp_url="rtmp://old/stream")
        ctrl._publisher_proc = MagicMock(poll=lambda: None)
        src = MagicMock(poll=lambda: None)
        src.args = ["ffmpeg", "-i", "rtmp://live/source"]
        ctrl._source_proc = src
        ctrl._index = 0
        ctrl._playlist = ["rtmp://live/source"]
        ctrl._loop_file = False

        ctrl.change_output("rtmp://new/stream")
        assert ctrl.rtmp_url == "rtmp://old/stream"
        assert ctrl._pending_rtmp_url == "rtmp://new/stream"
        assert ctrl.output_change_pending() is True
        assert ctrl.output_change_waiting_for_boundary() is True
        print("✓ test_change_output_when_live_playing")

def test_change_output_when_loop_file():
    """Loop file: change should wait for loop boundary."""
    with patch('subprocess.Popen'):
        ctrl = FFmpegController(rtmp_url="rtmp://old/stream")
        ctrl._publisher_proc = MagicMock(poll=lambda: None)
        src = MagicMock(poll=lambda: None)
        src.args = ["ffmpeg", "-i", "/path/video.mp4"]
        ctrl._source_proc = src
        ctrl._index = 0
        ctrl._playlist = ["/path/video.mp4"]
        ctrl._loop_file = True

        ctrl.change_output("rtmp://new/stream")
        assert ctrl.rtmp_url == "rtmp://old/stream"
        assert ctrl._pending_rtmp_url == "rtmp://new/stream"
        assert ctrl.output_change_waiting_for_boundary() is True
        print("✓ test_change_output_when_loop_file")

def test_same_url_no_pending():
    """Setting same URL should clear any pending."""
    with patch('subprocess.Popen'):
        ctrl = FFmpegController(rtmp_url="rtmp://old/stream")
        ctrl._publisher_proc = MagicMock(poll=lambda: None)
        ctrl._source_proc = MagicMock(poll=lambda: None)
        ctrl._index = 0
        ctrl._playlist = ["/path/video.mp4"]
        ctrl._loop_file = False

        ctrl.change_output("rtmp://old/stream")
        assert ctrl._pending_rtmp_url is None
        print("✓ test_same_url_no_pending")

def test_pending_at_idle():
    """_pending_at_idle should be true only when no loop, no live, no source."""
    with patch('subprocess.Popen'):
        ctrl = FFmpegController(rtmp_url="rtmp://old/stream")
        ctrl._publisher_proc = MagicMock(poll=lambda: None)
        ctrl._source_proc = None
        ctrl._index = None
        ctrl._playlist = []
        ctrl._loop_file = False
        ctrl._pending_rtmp_url = "rtmp://new/stream"

        assert ctrl._pending_at_idle() is True

        ctrl._loop_file = True
        assert ctrl._pending_at_idle() is False

        ctrl._loop_file = False
        ctrl._index = 0
        ctrl._playlist = ["rtmp://live/source"]
        assert ctrl._pending_at_idle() is False

        print("✓ test_pending_at_idle")

if __name__ == "__main__":
    test_change_output_when_no_source()
    test_change_output_when_source_idle()
    test_change_output_when_file_playing()
    test_change_output_when_live_playing()
    test_change_output_when_loop_file()
    test_same_url_no_pending()
    test_pending_at_idle()
    print("\nAll tests passed!")