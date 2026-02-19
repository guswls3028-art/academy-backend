# libs/s3_client/presign.py

from django.conf import settings
import boto3
from botocore.client import Config

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

PRESIGN_UPLOAD_EXPIRES = 900        # 15 min
PRESIGN_STREAM_EXPIRES = 60 * 60    # 1 hour

# ---------------------------------------------------------------------
# S3 Client (Cloudflare R2)
# ---------------------------------------------------------------------

_s3 = boto3.client(
    "s3",
    region_name="auto",
    endpoint_url=settings.R2_ENDPOINT,
    aws_access_key_id=settings.R2_ACCESS_KEY,
    aws_secret_access_key=settings.R2_SECRET_KEY,
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},  # ✅ 로컬/운영 모두 안정적
    ),
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _get_bucket() -> str:
    bucket = getattr(settings, "R2_VIDEO_BUCKET", None)
    if not bucket:
        raise RuntimeError("R2_VIDEO_BUCKET is not set in Django settings")
    return bucket

# ---------------------------------------------------------------------
# Presigned URLs
# ---------------------------------------------------------------------

def create_presigned_put_url(
    key: str,
    content_type: str,
    expires_in: int = PRESIGN_UPLOAD_EXPIRES,
) -> str:
    """
    R2 presigned PUT URL 생성 (비디오 업로드용)
    CORS 헤더 포함하여 브라우저에서 직접 업로드 가능하도록 설정
    """
    return _s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": _get_bucket(),
            "Key": key,
            "ContentType": content_type,  # 🔥 핵심
            # CORS를 위한 헤더 (R2 버킷 CORS 설정과 함께 사용)
            "Metadata": {},
        },
        ExpiresIn=expires_in,
    )



def create_presigned_get_url(
    key: str,
    expires_in: int = PRESIGN_STREAM_EXPIRES,
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
