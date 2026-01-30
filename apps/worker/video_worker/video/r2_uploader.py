from __future__ import annotations

import os
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig

from apps.worker.video_worker.utils import guess_content_type, cache_control_for_object, trim_tail, backoff_sleep


class UploadError(RuntimeError):
    pass


def upload_directory(
    *,
    local_dir: Path,
    bucket: str,
    prefix: str,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str,
    max_concurrency: int,
    retry_max: int = 5,
    backoff_base: float = 0.5,
    backoff_cap: float = 10.0,
) -> None:
    """
    업로드 정책 (요구사항 반영):
    - Content-Type 정확히
    - Cache-Control 전략 포함
      - .m3u8 : no-cache
      - .ts   : public, max-age=31536000, immutable
      - thumb : 7d
    - 부분 업로드 방지:
      - boto3 multipart 실패 시 예외 / retry
      - 동일 Key에 overwrite는 허용 (idempotent)
    """
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    transfer_cfg = TransferConfig(
        max_concurrency=max_concurrency,
        multipart_threshold=8 * 1024 * 1024,
        multipart_chunksize=8 * 1024 * 1024,
        use_threads=True,
    )

    local_dir = local_dir.resolve()

    for root, _, files in os.walk(local_dir):
        for name in files:
            full_path = Path(root) / name
            rel = full_path.relative_to(local_dir)
            key = f"{prefix.rstrip('/')}/{rel.as_posix()}"

            extra = {
                "ContentType": guess_content_type(name),
                "CacheControl": cache_control_for_object(name),
            }

            attempt = 0
            while True:
                try:
                    s3.upload_file(
                        Filename=str(full_path),
                        Bucket=bucket,
                        Key=key,
                        ExtraArgs=extra,
                        Config=transfer_cfg,
                    )
                    break
                except Exception as e:
                    attempt += 1
                    if attempt >= retry_max:
                        raise UploadError(f"upload failed key={key} err={trim_tail(str(e))}") from e
                    backoff_sleep(attempt, backoff_base, backoff_cap)
