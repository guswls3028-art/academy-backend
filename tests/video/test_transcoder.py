from unittest import TestCase

from academy.adapters.video.transcoder import build_ffmpeg_command


class TranscoderCommandTests(TestCase):
    def command(self, *, dual, audio):
        return build_ffmpeg_command(
            input_path="source.mp4",
            hi_w=1920,
            hi_h=1080,
            mid_w=1280,
            mid_h=720,
            two_variants=dual,
            with_audio=audio,
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
