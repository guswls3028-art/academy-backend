import os
import boto3
from botocore.client import Config

# ---------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------

AWS_REGION = os.getenv("AWS_REGION", "auto")
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL")

AWS_PRESIGN_UPLOAD_EXPIRES = int(os.getenv("AWS_PRESIGN_UPLOAD_EXPIRES", "900"))
AWS_PRESIGN_STREAM_EXPIRES = int(os.getenv("AWS_PRESIGN_STREAM_EXPIRES", "3600"))

# ---------------------------------------------------------------------
# S3 Client (Cloudflare R2 – virtual host style 필수)
# ---------------------------------------------------------------------

_s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=AWS_S3_ENDPOINT_URL,
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "virtual"},
    ),
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _get_bucket() -> str:
    bucket = os.getenv("AWS_S3_BUCKET_NAME")
    if not bucket:
        raise RuntimeError("AWS_S3_BUCKET_NAME is not set")
    return bucket

# ---------------------------------------------------------------------
# Presigned URLs
# ---------------------------------------------------------------------

def create_presigned_put_url(
    key: str,
    expires_in: int = AWS_PRESIGN_UPLOAD_EXPIRES,
) -> str:
    """
    Generate presigned PUT url (upload)
    """
    return _s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": _get_bucket(),
            "Key": key,
        },
        ExpiresIn=expires_in,
    )


def create_presigned_get_url(
    key: str,
    expires_in: int = AWS_PRESIGN_STREAM_EXPIRES,
) -> str:
    """
    Generate presigned GET url (download / stream)
    """
    return _s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": _get_bucket(),
            "Key": key,
        },
        ExpiresIn=expires_in,
    )
