import boto3
import os
from pathlib import Path
import mimetypes

R2_ENDPOINT = os.getenv("R2_ENDPOINT")  # 예: https://xxxx.r2.cloudflarestorage.com
R2_BUCKET = os.getenv("R2_BUCKET", "academy-video")

R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
)

def upload_dir(local_dir: Path, prefix: str):
    """
    local_dir 전체를 prefix 기준으로 R2에 업로드
    """
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue

        key = f"{prefix}/{path.relative_to(local_dir)}"
        content_type, _ = mimetypes.guess_type(path.name)

        s3.upload_file(
            str(path),
            R2_BUCKET,
            key,
            ExtraArgs={
                "ContentType": content_type or "application/octet-stream"
            },
        )
