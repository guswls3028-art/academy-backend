# libs/r2_client/presign.py

from django.conf import settings
import boto3
from botocore.client import Config

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

PRESIGN_UPLOAD_EXPIRES = 7200       # 2 hours — large video uploads need ample time
PRESIGN_STREAM_EXPIRES = 60 * 60    # 1 hour

# ---------------------------------------------------------------------
# Cloudflare R2 Client (S3-compatible API)
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
    return _s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": _get_bucket(),
            "Key": key,
            "ContentType": content_type,  # 🔥 핵심
        },
        ExpiresIn=expires_in,
    )



# ---------------------------------------------------------------------
# Multipart Upload
# ---------------------------------------------------------------------

def create_multipart_upload(key: str, content_type: str) -> str:
    """R2 multipart upload 생성 → UploadId 반환."""
    resp = _s3.create_multipart_upload(
        Bucket=_get_bucket(),
        Key=key,
        ContentType=content_type,
    )
    return resp["UploadId"]


def create_presigned_upload_part_url(
    key: str,
    upload_id: str,
    part_number: int,
    expires_in: int = 3600,  # 1 hour per part — 100MB 파트에 충분
) -> str:
    """개별 파트 업로드용 presigned URL 생성."""
    return _s3.generate_presigned_url(
        ClientMethod="upload_part",
        Params={
            "Bucket": _get_bucket(),
            "Key": key,
            "UploadId": upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=expires_in,
    )


def complete_multipart_upload(key: str, upload_id: str, parts: list[dict]) -> dict:
    """
    Multipart upload 완료.
    parts: [{"ETag": "...", "PartNumber": 1}, ...]
    """
    return _s3.complete_multipart_upload(
        Bucket=_get_bucket(),
        Key=key,
        UploadId=upload_id,
        MultipartUpload={"Parts": sorted(parts, key=lambda p: p["PartNumber"])},
    )


def abort_multipart_upload(key: str, upload_id: str) -> None:
    """Multipart upload 중단 — 불완전 파트 정리."""
    try:
        _s3.abort_multipart_upload(
            Bucket=_get_bucket(),
            Key=key,
            UploadId=upload_id,
        )
    except Exception:
        pass  # best-effort cleanup


# ---------------------------------------------------------------------
# Presigned GET
# ---------------------------------------------------------------------

def create_presigned_get_url(
    key: str,
    expires_in: int = PRESIGN_STREAM_EXPIRES,
    bucket: str | None = None,
) -> str:
    """
    Generate presigned GET url (download / stream)
    """
    return _s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": bucket or _get_bucket(),
            "Key": key,
        },
        ExpiresIn=expires_in,
    )
