# apps/worker/media/r2_uploader.py

import boto3
import mimetypes
from pathlib import Path
from django.conf import settings

# ---------------------------------------------------------------------
# R2 S3 Client (공용)
# ---------------------------------------------------------------------

s3 = boto3.client(
    "s3",
    endpoint_url=settings.R2_ENDPOINT,
    aws_access_key_id=settings.R2_ACCESS_KEY,
    aws_secret_access_key=settings.R2_SECRET_KEY,
    region_name="auto",
)

# ---------------------------------------------------------------------
# Upload helpers (기존)
# ---------------------------------------------------------------------

def upload_fileobj_to_r2(*, fileobj, key: str, content_type: str | None = None):
    """
    Django UploadedFile -> R2 업로드
    """
    s3.upload_fileobj(
        Fileobj=fileobj,
        Bucket=settings.R2_BUCKET,
        Key=key,
        ExtraArgs={
            "ContentType": content_type or "application/octet-stream"
        },
    )


def upload_dir(local_dir: Path, prefix: str):
    """
    local_dir 전체를 prefix 기준으로 R2에 업로드
    """
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue

        relative_path = path.relative_to(local_dir).as_posix()
        key = f"{prefix}/{relative_path}"

        content_type, _ = mimetypes.guess_type(path.name)

        s3.upload_file(
            str(path),
            settings.R2_BUCKET,
            key,
            ExtraArgs={
                "ContentType": content_type or "application/octet-stream"
            },
        )

# ---------------------------------------------------------------------
# ⭐ STEP 2 핵심: presigned GET URL 생성
# ---------------------------------------------------------------------

def generate_presigned_get_url(
    *,
    key: str,
    expires_in: int = 3600,
) -> str:
    """
    R2 object를 임시로 다운로드할 수 있는 presigned GET URL 생성

    - API 서버에서만 호출
    - worker는 이 URL만 사용 (R2 credential 불필요)
    """
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": settings.R2_BUCKET,
            "Key": key,
        },
        ExpiresIn=expires_in,
    )
