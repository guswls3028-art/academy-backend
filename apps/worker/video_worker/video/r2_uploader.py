from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig

from apps.worker.video_worker.utils import guess_content_type, cache_control_for_object, trim_tail, backoff_sleep

logger = logging.getLogger(__name__)


class UploadError(RuntimeError):
    pass


class UploadIntegrityError(RuntimeError):
    """R2 업로드 후 무결성 검증 실패 (master.m3u8 또는 세그먼트 누락)."""
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

    # Collect all files first for progress logging
    all_files = []
    for root, _, files in os.walk(local_dir):
        for name in files:
            full_path = Path(root) / name
            rel = full_path.relative_to(local_dir)
            key = f"{prefix.rstrip('/')}/{rel.as_posix()}"
            all_files.append((full_path, key, name))

    total = len(all_files)
    logger.info("[R2_UPLOAD] Starting upload: %d files to %s", total, prefix)

    for idx, (full_path, key, name) in enumerate(all_files, 1):
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

        # Log progress every 500 files or at start/end
        if idx == 1 or idx == total or idx % 500 == 0:
            logger.info("[R2_UPLOAD] Progress: %d/%d files uploaded", idx, total)

    logger.info("[R2_UPLOAD] Upload complete: %d files", total)


def _s3_client(endpoint_url: str, access_key: str, secret_key: str, region: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )


def list_prefix(
    bucket: str,
    prefix: str,
    *,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str,
) -> list[str]:
    keys = []
    client = _s3_client(endpoint_url, access_key, secret_key, region)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            k = obj.get("Key")
            if k:
                keys.append(k)
    return keys


def delete_prefix(
    bucket: str,
    prefix: str,
    *,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str,
) -> None:
    keys = list_prefix(bucket=bucket, prefix=prefix, endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, region=region)
    if not keys:
        return
    client = _s3_client(endpoint_url, access_key, secret_key, region)
    for i in range(0, len(keys), 1000):
        chunk = keys[i : i + 1000]
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in chunk]})


def publish_tmp_to_final(
    bucket: str,
    tmp_prefix: str,
    final_prefix: str,
    *,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str,
    max_workers: int = 16,
) -> None:
    """
    tmp → final 병렬 복사. 기존 순차 copy_object를 ThreadPoolExecutor로 병렬화.
    4000+ 세그먼트도 수 분 내 완료.
    """
    tmp_prefix = tmp_prefix.rstrip("/") + "/"
    final_prefix = final_prefix.rstrip("/") + "/"
    keys = list_prefix(bucket=bucket, prefix=tmp_prefix, endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, region=region)

    total = len(keys)
    logger.info("[R2_PUBLISH] Starting publish: %d files from %s -> %s", total, tmp_prefix, final_prefix)

    if total == 0:
        return

    def _copy_one(key: str) -> str:
        if not key.startswith(tmp_prefix):
            return key
        rel = key[len(tmp_prefix):]
        dest_key = final_prefix + rel
        client = _s3_client(endpoint_url, access_key, secret_key, region)
        client.copy_object(
            CopySource={"Bucket": bucket, "Key": key},
            Bucket=bucket,
            Key=dest_key,
        )
        return dest_key

    copied = 0
    errors = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_copy_one, key): key for key in keys}
        for future in as_completed(futures):
            copied += 1
            try:
                future.result()
            except Exception as e:
                src_key = futures[future]
                errors.append(f"{src_key}: {e}")
                logger.warning("[R2_PUBLISH] copy failed: %s -> %s", src_key, e)

            if copied == 1 or copied == total or copied % 500 == 0:
                logger.info("[R2_PUBLISH] Progress: %d/%d files copied", copied, total)

    if errors:
        raise UploadError(f"publish_tmp_to_final failed for {len(errors)} files: {errors[0]}")

    logger.info("[R2_PUBLISH] Publish complete: %d files. Cleaning tmp...", total)
    delete_prefix(bucket=bucket, prefix=tmp_prefix, endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, region=region)
    logger.info("[R2_PUBLISH] Tmp cleaned: %s", tmp_prefix)


def verify_hls_integrity_r2(
    bucket: str,
    final_prefix: str,
    *,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str,
    min_segments: int = 3,
) -> None:
    client = _s3_client(endpoint_url, access_key, secret_key, region)
    prefix = final_prefix.rstrip("/") + "/"
    master_key = prefix + "master.m3u8"
    try:
        resp = client.get_object(Bucket=bucket, Key=master_key)
        body = resp["Body"].read().decode("utf-8", errors="replace")
    except Exception:
        raise UploadIntegrityError("master.m3u8 missing")
    lines = [l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith("#")]
    segment_count = 0
    for line in lines:
        if line.endswith(".m3u8"):
            variant_key = prefix + line
            try:
                vr = client.get_object(Bucket=bucket, Key=variant_key)
                vbody = vr["Body"].read().decode("utf-8", errors="replace")
            except Exception:
                raise UploadIntegrityError(f"variant playlist missing: {line}")
            for vline in vbody.splitlines():
                vline = vline.strip()
                if vline and not vline.startswith("#") and vline.endswith(".ts"):
                    segment_count += 1
                    seg_key = variant_key.rsplit("/", 1)[0] + "/" + vline
                    try:
                        client.head_object(Bucket=bucket, Key=seg_key)
                    except Exception:
                        raise UploadIntegrityError(f"segment missing: {seg_key}")
    if segment_count < min_segments:
        raise UploadIntegrityError(f"segment count {segment_count} < min_segments {min_segments}")
