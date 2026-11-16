from io import StringIO
import json
import os
from pathlib import Path
from shutil import which
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch

from academy.adapters.video.transcoder import (
    FFmpegProcessError,
    _probe_audio_stream_index,
    build_ffmpeg_command,
    transcode_to_hls,
)


class TranscoderCommandTests(TestCase):
    def command(self, *, dual, audio):
        return build_ffmpeg_command(
            input_path="source.mp4",
            hi_w=1920,
            hi_h=1080,
            mid_w=1280,
            mid_h=720,
            two_variants=dual,
            audio_stream_index=2 if audio else None,
            ffmpeg_bin="ffmpeg",
            hls_time=4,
        )

    def test_dual_variants_keep_distinct_encoder_tuning(self):
        command = self.command(dual=True, audio=True)
        for option, value in {
            "-profile:v:0": "high", "-profile:v:1": "main",
            "-crf:v:0": "18", "-crf:v:1": "21", "-refs:v:0": "4",
            "-maxrate:v:0": "12000k", "-maxrate:v:1": "5000k",
            "-bufsize:v:0": "18000k", "-bufsize:v:1": "7500k",
            "-b:a:0": "128k", "-b:a:1": "128k",
            "-c:v": "libx264", "-pix_fmt": "yuv420p", "-preset": "slow",
            "-g": "48", "-keyint_min": "48", "-sc_threshold": "0",
            "-c:a": "aac", "-ac": "2",
            "-var_stream_map": "v:0,a:0,name:2 v:1,a:1,name:1",
        }.items():
            with self.subTest(option=option):
                self.assertEqual(command.count(option), 1)
                self.assertEqual(command[command.index(option) + 1], value)
        self.assertNotIn("-profile:v", command)
        self.assertNotIn("-crf", command)
        self.assertNotIn("-refs:v:1", command)

    def test_each_output_allows_native_frame_rate_and_automatic_h264_level(self):
        for dual in (False, True):
            with self.subTest(dual=dual):
                command = self.command(dual=dual, audio=False)
                self.assertFalse(any(option.split(":")[0] in {"-level", "-r"} for option in command))
                self.assertNotIn("-c:a", command)
                filter_graph = command[command.index("-filter_complex" if dual else "-vf") + 1]
                self.assertIn("scale=1920:1080,setsar=1", filter_graph)
                self.assertNotIn("fps=", filter_graph)
                if dual:
                    self.assertIn("scale=1280:720,setsar=1", filter_graph)
                else:
                    self.assertEqual(command[command.index("-profile:v") + 1], "high")
                    self.assertEqual(command[command.index("-crf") + 1], "18")
                    self.assertEqual(command[command.index("-refs:v") + 1], "4")

    def test_maps_only_the_selected_audio_stream_once_per_variant(self):
        for dual in (False, True):
            with self.subTest(dual=dual):
                command = self.command(dual=dual, audio=True)
                maps = [command[i + 1] for i, arg in enumerate(command) if arg == "-map"]
                expected = ["[vhiout]", "0:2", "[vmidout]", "0:2"] if dual else ["0:v:0", "0:2"]
                self.assertEqual(maps, expected)

    def test_probe_prefers_default_audio_and_falls_back_to_first_stream(self):
        first = {"codec_type": "audio", "index": 2, "disposition": {"default": 0}}
        second = {"codec_type": "audio", "index": 5, "disposition": {"default": 1}}
        with patch("academy.adapters.video.transcoder.subprocess.run") as run:
            for streams, expected in (
                ([first, second], 5),
                ([first, {**second, "disposition": {"default": 0}}], 2),
                ([{"codec_type": "video", "index": 0}], None),
            ):
                with self.subTest(expected=expected):
                    run.return_value.returncode = 0
                    run.return_value.stdout = json.dumps({"streams": streams})
                    self.assertEqual(
                        _probe_audio_stream_index(input_path="source.mp4", ffprobe_bin="ffprobe", timeout=5),
                        expected,
                    )

    @patch("academy.adapters.video.transcoder._probe_audio_stream_index", return_value=None)
    @patch("academy.adapters.video.transcoder._probe_resolution", return_value=(0, 0))
    @patch("academy.adapters.video.transcoder.subprocess.Popen")
    def test_failure_retains_bounded_redacted_diagnostics_with_or_without_duration(self, popen, _, __):
        for duration in (None, 120):
            with self.subTest(duration=duration), TemporaryDirectory() as directory:
                src = str(Path(directory) / "private-source.mp4")
                process = popen.return_value.__enter__.return_value
                process.returncode = 1
                process.stdout = StringIO("out_time_ms=2000000\n")
                process.stderr = StringIO(
                    f"{src}: Input/output error\n"
                    "Error opening https://storage.example/private-key?token=secret-value\n"
                    + ("x" * 400 + "\n") * 10
                    + "Authorization: Bearer secret-value\n"
                )
                progress = []
                with self.assertRaises(FFmpegProcessError) as caught:
                    transcode_to_hls(
                        video_id=1,
                        input_path=src,
                        output_root=Path(directory) / "hls",
                        ffmpeg_bin="ffmpeg",
                        ffprobe_bin="ffprobe",
                        hls_time=4,
                        timeout=45,
                        duration_sec=duration,
                        progress_callback=lambda current, total: progress.append((current, total)),
                    )
                error = caught.exception
                self.assertEqual(error.exit_code, 1)
                self.assertFalse(error.invalid_source)
                self.assertEqual(error.diagnostics[:2], ("<path>: Input/output error", "Error opening <url>"))
                self.assertEqual(len(error.diagnostics), 8)
                self.assertEqual(error.diagnostics[-1], "<redacted>")
                self.assertTrue(all(len(line) <= 300 for line in error.diagnostics))
                self.assertNotIn("secret-value", str(error))
                self.assertEqual(progress, [(2.0, 120.0)] if duration else [])

    @skipUnless(which("ffmpeg") and which("ffprobe"), "requires FFmpeg and ffprobe")
    def test_audio_only_source_is_a_confirmed_permanent_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "audio-only.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", str(source)],
                check=True, capture_output=True, timeout=15,
            )
            with self.assertRaises(FFmpegProcessError) as caught:
                transcode_to_hls(
                    video_id=1, input_path=str(source), output_root=root / "hls",
                    ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", hls_time=4, timeout=45,
                )
            self.assertTrue(caught.exception.invalid_source, caught.exception.diagnostics)
            self.assertGreater(caught.exception.exit_code, 0)

    @skipUnless(which("ffmpeg") and which("ffprobe"), "requires FFmpeg and ffprobe")
    def test_short_360p_sources_decode_without_upscaling_or_extra_audio(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for track_count in (0, 1, 2):
                with self.subTest(track_count=track_count):
                    source = root / f"source-{track_count}.mp4"
                    command = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=12"]
                    for frequency in (440, 550)[:track_count]:
                        command += ["-f", "lavfi", "-i", f"sine=frequency={frequency}"]
                    command += ["-map", "0:v:0"]
                    for audio_input in range(1, track_count + 1):
                        command += ["-map", f"{audio_input}:a:0"]
                    if track_count == 2:
                        command += ["-disposition:a:0", "0", "-disposition:a:1", "default"]
                    subprocess.run(
                        [*command, "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)],
                        check=True, capture_output=True, timeout=30,
                    )
                    output = transcode_to_hls(
                        video_id=1, input_path=str(source), output_root=root / f"hls-{track_count}",
                        ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", hls_time=4, timeout=45, duration_sec=1,
                    )
                    variants = [line for line in output.read_text().splitlines() if line and not line.startswith("#")]
                    self.assertEqual(variants, ["v1/index.m3u8"])
                    playlist = str(output.parent / variants[0])
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", playlist],
                        check=True, capture_output=True, timeout=15,
                    )
                    streams = json.loads(probe.stdout)["streams"]
                    self.assertEqual(streams[0], {"codec_name": "h264", "width": 640, "height": 360})
                    self.assertEqual([item["codec_name"] for item in streams[1:]], ["aac"] if track_count else [])
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-i", playlist, "-f", "null", os.devnull],
                        check=True, capture_output=True, timeout=15,
                    )
