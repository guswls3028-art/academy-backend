# apps/worker/media/r2_uploader.py

import boto3
import mimetypes
from pathlib import Path
from django.conf import settings

s3 = boto3.client(
    "s3",
    endpoint_url=settings.R2_ENDPOINT,
    aws_access_key_id=settings.R2_ACCESS_KEY,
    aws_secret_access_key=settings.R2_SECRET_KEY,
    region_name="auto",
)

def upload_dir(local_dir: Path, prefix: str):
    """
    local_dir 전체를 prefix 기준으로 R2에 업로드
    (Windows 경로 문제 방지용 표준형)
    """
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue

        # 🔥🔥🔥 핵심
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
