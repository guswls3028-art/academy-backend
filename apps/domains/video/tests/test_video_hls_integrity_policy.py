from __future__ import annotations

from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from academy.adapters.video.r2_uploader import UploadIntegrityError, verify_hls_integrity_r2
from academy.adapters.video.validate import effective_min_segments, validate_hls_output


class _FakeR2Client:
    def __init__(self, objects: dict[str, bytes | str]):
        self.objects = {
            key: value.encode("utf-8") if isinstance(value, str) else value
            for key, value in objects.items()
        }

    def get_object(self, *, Bucket, Key):  # noqa: N803 - boto3 keyword casing
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": BytesIO(self.objects[Key])}

    def head_object(self, *, Bucket, Key):  # noqa: N803 - boto3 keyword casing
        if Key not in self.objects:
            raise KeyError(Key)
        return {}


def _single_segment_r2(prefix: str = "media/hls/videos/1/2") -> _FakeR2Client:
    base = prefix.rstrip("/")
    return _FakeR2Client(
        {
            f"{base}/master.m3u8": "#EXTM3U\nv1/index.m3u8\n",
            f"{base}/v1/index.m3u8": "#EXTM3U\n#EXTINF:4.0,\nseg0.ts\n",
            f"{base}/v1/seg0.ts": b"segment",
            f"{base}/thumbnail.jpg": b"thumb",
        }
    )


class VideoHlsIntegrityPolicyTests(TestCase):
    def test_effective_min_segments_allows_short_clip(self):
        self.assertEqual(
            effective_min_segments(3, duration_seconds=4, hls_time_seconds=4),
            1,
        )

    def test_effective_min_segments_keeps_floor_for_long_clip(self):
        self.assertEqual(
            effective_min_segments(3, duration_seconds=60, hls_time_seconds=4),
            3,
        )

    def test_local_validator_accepts_short_single_segment_variant(self):
        with TemporaryDirectory() as tmp:
            from pathlib import Path

            root = Path(tmp)
            (root / "master.m3u8").write_text("#EXTM3U\nv1/index.m3u8\n", encoding="utf-8")
            (root / "v1").mkdir()
            (root / "v1" / "index.m3u8").write_text("#EXTM3U\nseg0.ts\n", encoding="utf-8")
            (root / "v1" / "seg0.ts").write_bytes(b"segment")

            validate_hls_output(root, 3, duration_seconds=4, hls_time_seconds=4)

    def test_local_validator_rejects_long_single_segment_variant(self):
        with TemporaryDirectory() as tmp:
            from pathlib import Path

            root = Path(tmp)
            (root / "master.m3u8").write_text("#EXTM3U\nv1/index.m3u8\n", encoding="utf-8")
            (root / "v1").mkdir()
            (root / "v1" / "index.m3u8").write_text("#EXTM3U\nseg0.ts\n", encoding="utf-8")
            (root / "v1" / "seg0.ts").write_bytes(b"segment")

            with self.assertRaisesRegex(RuntimeError, "segments=1 min=3"):
                validate_hls_output(root, 3, duration_seconds=60, hls_time_seconds=4)

    @patch("academy.adapters.video.r2_uploader._s3_client")
    def test_r2_integrity_accepts_short_single_segment_variant(self, mock_s3_client):
        mock_s3_client.return_value = _single_segment_r2()

        verify_hls_integrity_r2(
            "bucket",
            "media/hls/videos/1/2",
            endpoint_url="https://r2.example",
            access_key="access",
            secret_key="secret",
            region="auto",
            min_segments=3,
            duration_seconds=4,
            hls_time_seconds=4,
        )

    @patch("academy.adapters.video.r2_uploader._s3_client")
    def test_r2_integrity_rejects_long_single_segment_variant(self, mock_s3_client):
        mock_s3_client.return_value = _single_segment_r2()

        with self.assertRaisesRegex(UploadIntegrityError, "variant segment count 1 < min_segments 3"):
            verify_hls_integrity_r2(
                "bucket",
                "media/hls/videos/1/2",
                endpoint_url="https://r2.example",
                access_key="access",
                secret_key="secret",
                region="auto",
                min_segments=3,
                duration_seconds=60,
                hls_time_seconds=4,
            )
