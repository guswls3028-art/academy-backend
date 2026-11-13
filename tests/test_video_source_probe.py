import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch

from academy.adapters.video import source_probe


class SourceProbeTests(TestCase):
    @skipUnless(which("ffprobe"), "ffprobe executable is required for the timeout regression")
    def test_stalled_source_returns_for_encoding_worker_fallback(self):
        entered = threading.Event()
        release = threading.Event()

        class StalledSource(BaseHTTPRequestHandler):
            def do_GET(self):
                entered.set()
                release.wait(4)

            def log_message(self, *_args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), StalledSource) as server:
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            try:
                with patch.object(source_probe, "SOURCE_PROBE_TIMEOUT_SECONDS", 1):
                    result = source_probe.validate_source_media_via_ffprobe(
                        f"http://127.0.0.1:{server.server_port}/lesson.mp4",
                    )
            finally:
                release.set()
                server.shutdown()
                server_thread.join(2)

        self.assertTrue(entered.is_set())
        self.assertEqual(result, (False, {}, "ffprobe_timeout"))

    @skipUnless(which("ffmpeg") and which("ffprobe"), "FFmpeg executables are required for a real media probe")
    def test_source_metadata_from_real_video(self):
        with TemporaryDirectory() as root:
            source = str(Path(root) / "lesson.mp4")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "color=c=blue:s=64x64:r=2:d=3", "-c:v", "mpeg4", source,
            ], check=True, capture_output=True, timeout=15)
            self.assertEqual(
                source_probe.validate_source_media_via_ffprobe(source),
                (True, {"duration": 3, "has_video": True}, ""),
            )

    @patch("academy.adapters.video.source_probe.subprocess.run")
    def test_failed_probe_keeps_signed_url_out_of_returned_reason(self, run):
        run.side_effect = subprocess.CalledProcessError(
            1, ["ffprobe", "https://fixture.test/video?signature=synthetic"],
        )
        result = source_probe.validate_source_media_via_ffprobe("https://fixture.test/video?signature=synthetic")
        self.assertEqual(result, (False, {}, "ffprobe_failed"))

    @patch("academy.adapters.video.source_probe.subprocess.run")
    def test_duration_and_video_presence_remain_separate(self, run):
        response = {"format": {"duration": "1.75"}, "streams": [{"codec_type": "audio"}]}
        run.return_value = subprocess.CompletedProcess(["ffprobe"], 0, stdout=json.dumps(response).encode())
        self.assertEqual(
            source_probe.validate_source_media_via_ffprobe("lesson.mp4"),
            (False, {"duration": 1, "has_video": False}, "no_video_stream"),
        )
