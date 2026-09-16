from __future__ import annotations

import unittest
from unittest.mock import patch

from apps.infrastructure.storage.r2 import upload_fileobj_to_r2


class UploadFileobjToR2TimeoutTests(unittest.TestCase):
    """A stalled R2 connection must fail fast, not hang on boto3/botocore's
    own unbounded defaults (60s connect, 60s read, up to 5 retries) -- see
    apps/infrastructure/storage/r2.py's _get_s3_client. Guards the
    timeout_seconds forwarding itself, independent of any particular caller.
    Lives under submissions/tests (a covered coverage shard;
    apps/infrastructure has none -- see tests/test_test_suite_governance.py)
    since SubmissionCreateSerializer.create() is the caller this fix targets.
    """

    @patch("apps.infrastructure.storage.r2._get_s3_client")
    def test_bounded_timeout_reaches_the_s3_client(self, get_s3_client):
        upload_fileobj_to_r2(fileobj=object(), key="k", timeout_seconds=30)
        get_s3_client.assert_called_once_with(timeout_seconds=30)

    @patch("apps.infrastructure.storage.r2._get_s3_client")
    def test_omitted_timeout_still_forwards_none_explicitly(self, get_s3_client):
        # Not a recommendation -- documents that an omitted timeout_seconds
        # stays unbounded (the exact defect this fix targets at its one
        # call site). Any new caller must pass a real value.
        upload_fileobj_to_r2(fileobj=object(), key="k")
        get_s3_client.assert_called_once_with(timeout_seconds=None)

    @patch("apps.infrastructure.storage.r2._get_s3_client")
    def test_single_put_max_bytes_forces_a_transfer_config_above_it(self, get_s3_client):
        # timeout_seconds bounds one HTTP request. A file over boto3's default
        # multipart_threshold (8MB) silently becomes several requests
        # (CreateMultipartUpload + UploadPart(s) + CompleteMultipartUpload),
        # each individually bounded but not the total -- exactly the gap that
        # let OMR uploads still exceed a 90s caller-side timeout after
        # timeout_seconds=30 was added. single_put_max_bytes must force a
        # single PUT for any file at or under the caller's known max size.
        s3 = get_s3_client.return_value
        upload_fileobj_to_r2(fileobj=object(), key="k", single_put_max_bytes=10 * 1024 * 1024)
        s3.upload_fileobj.assert_called_once()
        config = s3.upload_fileobj.call_args.kwargs["Config"]
        self.assertEqual(config.multipart_threshold, 10 * 1024 * 1024)
        self.assertFalse(config.use_threads)

    @patch("apps.infrastructure.storage.r2._get_s3_client")
    def test_omitted_single_put_max_bytes_passes_no_transfer_config(self, get_s3_client):
        # Every other existing caller of upload_fileobj_to_r2 must keep
        # boto3's own default behavior unless it opts in.
        s3 = get_s3_client.return_value
        upload_fileobj_to_r2(fileobj=object(), key="k")
        s3.upload_fileobj.assert_called_once()
        self.assertNotIn("Config", s3.upload_fileobj.call_args.kwargs)
