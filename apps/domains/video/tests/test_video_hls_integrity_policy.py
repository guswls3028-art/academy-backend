from __future__ import annotations

from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from academy.adapters.video.r2_uploader import (
    UploadError, UploadIntegrityError, publish_tmp_to_final, verify_hls_integrity_r2,
)
from academy.adapters.video.validate import effective_min_segments, validate_hls_output


class _FakeR2Client:
    def __init__(self, objects: dict[str, bytes | str]):
        self.objects = {
            key: value.encode("utf-8") if isinstance(value, str) else value
            for key, value in objects.items()
        }
        self.list_pages = 0
        self.head_calls = 0
        self.fail_listing_page = None

    def get_object(self, *, Bucket, Key):  # noqa: N803 - boto3 keyword casing
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": BytesIO(self.objects[Key])}

    def head_object(self, *, Bucket, Key):  # noqa: N803 - boto3 keyword casing
        self.head_calls += 1
        if Key not in self.objects:
            raise KeyError(Key)
        return {}

    def get_paginator(self, operation):
        if operation != "list_objects_v2":
            raise AssertionError(operation)
        return self

    def paginate(self, *, Bucket, Prefix):  # noqa: N803 - boto3 keyword casing
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        for offset in range(0, max(1, len(keys)), 1000):
            self.list_pages += 1
            if self.list_pages == self.fail_listing_page:
                raise PermissionError("listing interrupted")
            yield {"Contents": [{"Key": key} for key in keys[offset:offset + 1000]]}


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
    def test_publish_reuses_copy_client_and_cleans_only_after_all_copies(self):
        keys = [f"tenants/17/tmp/job/seg{i}.ts" for i in range(100)]
        client = Mock()
        with (
            patch("academy.adapters.video.r2_uploader.list_prefix", return_value=keys),
            patch("academy.adapters.video.r2_uploader._s3_client", return_value=client) as factory,
            patch("academy.adapters.video.r2_uploader.delete_prefix") as cleanup,
        ):
            publish_tmp_to_final(
                "bucket", "tenants/17/tmp/job", "tenants/17/video/hls/23",
                endpoint_url="https://r2.example", access_key="access",
                secret_key="secret", region="auto", max_workers=4,
            )
            factory.assert_called_once_with("https://r2.example", "access", "secret", "auto", max_pool_connections=4)
            self.assertEqual(client.copy_object.call_count, 100)
            destinations = {call.kwargs["Key"] for call in client.copy_object.call_args_list}
            self.assertEqual(destinations, {key.replace("tmp/job/", "video/hls/23/") for key in keys})
            cleanup.assert_called_once()
            self.assertEqual(cleanup.call_args.kwargs["prefix"], "tenants/17/tmp/job/")

    def test_failed_copy_preserves_source_for_retry(self):
        client = Mock()
        client.copy_object.side_effect = RuntimeError("copy unavailable")
        with (
            patch("academy.adapters.video.r2_uploader.list_prefix", return_value=["tenants/17/tmp/job/seg0.ts"]),
            patch("academy.adapters.video.r2_uploader._s3_client", return_value=client),
            patch("academy.adapters.video.r2_uploader.delete_prefix") as cleanup,
        ):
            with self.assertRaisesRegex(UploadError, "publish_tmp_to_final failed"):
                publish_tmp_to_final(
                    "bucket", "tenants/17/tmp/job", "tenants/17/video/hls/23",
                    endpoint_url="https://r2.example", access_key="access",
                    secret_key="secret", region="auto",
                )
            cleanup.assert_not_called()

    def verify_r2(self, client, **kwargs):
        with patch("academy.adapters.video.r2_uploader._s3_client", return_value=client):
            verify_hls_integrity_r2(
                "bucket", "media/hls/videos/1/2",
                endpoint_url="https://r2.example", access_key="access",
                secret_key="secret", region="auto", **kwargs,
            )

    def long_video(self):
        client = _single_segment_r2()
        prefix = "media/hls/videos/1/2/v1/"
        segments = [f"seg{i:04d}.ts" for i in range(2500)]
        client.objects[prefix + "index.m3u8"] = ("#EXTM3U\n" + "\n".join(segments)).encode()
        client.objects.update({prefix + name: b"segment" for name in segments})
        return client

    def test_long_video_checks_all_pages_without_per_segment_requests(self):
        client = self.long_video()
        self.verify_r2(client)
        self.assertEqual(client.list_pages, 3)
        self.assertEqual(client.head_calls, 0)

    def test_late_missing_segment_is_rejected_despite_other_tenant_copy(self):
        client = self.long_video()
        missing = "media/hls/videos/1/2/v1/seg2499.ts"
        del client.objects[missing]
        client.objects[missing.replace("videos/1/", "videos/9/")] = b"other tenant"
        with self.assertRaisesRegex(UploadIntegrityError, "segment missing:.*seg2499.ts"):
            self.verify_r2(client)

    def test_partial_inventory_fails_closed(self):
        client = self.long_video()
        client.fail_listing_page = 2
        with self.assertRaisesRegex(UploadIntegrityError, "inventory unavailable"):
            self.verify_r2(client)

    def test_missing_thumbnail_is_rejected(self):
        client = self.long_video()
        del client.objects["media/hls/videos/1/2/thumbnail.jpg"]
        with self.assertRaisesRegex(UploadIntegrityError, "thumbnail.jpg missing"):
            self.verify_r2(client)

    def test_missing_playlist_is_rejected(self):
        for name in ("master.m3u8", "v1/index.m3u8"):
            with self.subTest(name=name):
                client = self.long_video()
                del client.objects["media/hls/videos/1/2/" + name]
                with self.assertRaises(UploadIntegrityError):
                    self.verify_r2(client)

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
