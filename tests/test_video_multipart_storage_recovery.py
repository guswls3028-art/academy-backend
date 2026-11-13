from unittest.mock import patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase, override_settings

from academy.adapters.storage.r2_objects import completed_multipart_object_size
from academy.adapters.storage.r2_presign import MultipartCompletionMismatch, complete_multipart_upload


@override_settings(R2_VIDEO_BUCKET="test-video-bucket")
class MultipartStorageReadbackTests(SimpleTestCase):
    @patch("academy.adapters.storage.r2_presign.completed_multipart_object_size", return_value=110_000_000)
    @patch("academy.adapters.storage.r2_presign._presign")
    def test_no_such_upload_requires_matching_stored_parts(self, provider, size):
        provider.return_value.complete_multipart_upload.side_effect = ClientError(
            {"Error": {"Code": "NoSuchUpload"}}, "CompleteMultipartUpload",
        )
        parts = [{"ETag": '"bce6bf66aeb76c7040fdd5f4eccb78e6"', "PartNumber": 1}]
        self.assertEqual(complete_multipart_upload("video.mp4", "upload-id", parts), 110_000_000)
        size.assert_called_once_with(key="video.mp4", parts=parts)
        size.return_value = None
        with self.assertRaises(MultipartCompletionMismatch):
            complete_multipart_upload("video.mp4", "upload-id", parts)

    @patch("academy.adapters.storage.r2_objects.video_bucket_client")
    def test_exact_key_and_ordered_part_etag_required(self, make_client):
        head = make_client.return_value.head_object
        head.return_value = {"ETag": '"f77dc0eecdebcd774a2a22cb393ad2ff-2"', "ContentLength": 110_000_000}
        parts = [
            {"ETag": '"bce6bf66aeb76c7040fdd5f4eccb78e6"', "PartNumber": 1},
            {"ETag": '"8165449fc15bbf43d3b674595cbcc406"', "PartNumber": 2},
        ]
        key = "tenants/10/video/raw/test.mp4"

        self.assertEqual(completed_multipart_object_size(key=key, parts=parts), 110_000_000)
        head.assert_called_once_with(Bucket="test-video-bucket", Key=key)
        self.assertIsNone(completed_multipart_object_size(key=key, parts=parts[::-1]))
        head.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")
        self.assertIsNone(completed_multipart_object_size(key=key, parts=parts))
