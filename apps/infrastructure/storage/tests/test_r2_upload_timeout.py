from __future__ import annotations

import unittest
from unittest.mock import patch

from apps.infrastructure.storage.r2 import upload_fileobj_to_r2


class UploadFileobjToR2TimeoutTests(unittest.TestCase):
    """A stalled R2 connection must fail fast, not hang on boto3/botocore's
    own unbounded defaults (60s connect, 60s read, up to 5 retries) -- see
    apps/infrastructure/storage/r2.py's _get_s3_client. Guards the
    timeout_seconds forwarding itself, independent of any particular caller.
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
