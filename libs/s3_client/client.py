# libs/s3_client/client.py

from typing import Tuple
from django.conf import settings
import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------
# S3 Client (Cloudflare R2)
# ---------------------------------------------------------------------

_s3 = boto3.client(
    "s3",
    region_name="auto",
    endpoint_url=settings.R2_ENDPOINT,
    aws_access_key_id=settings.R2_ACCESS_KEY,
    aws_secret_access_key=settings.R2_SECRET_KEY,
)

# ---------------------------------------------------------------------
# API
# ---------------------------------------------------------------------

def head_object(key: str) -> Tuple[bool, int]:
    """
    Check object exists + size (bytes)
    """
    try:
        resp = _s3.head_object(
            Bucket=settings.R2_BUCKET,
            Key=key,
        )
        return True, int(resp.get("ContentLength") or 0)

    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False, 0
        raise
