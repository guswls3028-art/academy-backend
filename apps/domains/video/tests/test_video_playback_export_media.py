import io
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch

from academy.adapters.video.playback_export import _best_variant, _segments, build_playback_mp4


class PlaybackExportMediaTests(TestCase):
    def test_remote_and_parent_paths_are_rejected_before_loading_segments(self):
        for path in ["../v1/index.m3u8", "https://outside.test/v1/index.m3u8", "v1/index.m3u8?a=b"]:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "unsupported_variant"):
                _best_variant(["#EXTM3U", "#EXT-X-STREAM-INF:BANDWIDTH=90000", path])
        for path in ["../index1.ts", "https://outside.test/index1.ts", "index1.ts?x=y"]:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "invalid_segment"):
                _segments(["#EXTM3U", "#EXTINF:2,", path, "#EXT-X-ENDLIST"])

    @skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
    def test_a_complete_h264_aac_hls_upload_remuxes_to_mp4(self):
        with TemporaryDirectory() as generated, TemporaryDirectory() as work:
            subprocess.run([
                "ffmpeg", "-nostdin", "-hide_banner", "-v", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=600:sample_rate=48000",
                "-t", "5", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-g", "24",
                "-c:a", "aac", "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod",
                "-hls_segment_filename", "v%v/index%d.ts", "-master_pl_name", "master.m3u8",
                "-var_stream_map", "v:0,a:0,name:1", "v%v/index.m3u8",
            ], cwd=generated, check=True, timeout=30, capture_output=True)

            class LocalBucket:
                def get_object(self, *, Bucket, Key):
                    self_test.assertEqual(Bucket, "test-video")
                    name = Key.removeprefix("tenants/1/video/hls/2/")
                    content = (Path(generated) / name).read_bytes()
                    return {"Body": io.BytesIO(content), "ContentLength": len(content)}

            self_test = self
            output = Path(work) / "export.mp4"
            with patch("academy.adapters.video.playback_export.video_bucket_client", return_value=LocalBucket()):
                build_playback_mp4(bucket="test-video", master_key="tenants/1/video/hls/2/master.m3u8", output=output)
            probe = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
                "-of", "csv=p=0", str(output),
            ], timeout=30).decode()
            self.assertEqual(set(probe.split()), {"h264", "aac"})
