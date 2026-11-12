"""Synthetic canary for the isolated homework-preview Batch runtime only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

FAILURE_CODES = frozenset({
    "development_batch_required", "candidate_environment_mismatch",
    "development_bucket_required", "general_video_must_remain_disabled",
    "development_database_required", "production_database_denial_unproven",
    "production_parameter_denial_unproven", "production_parameter_access_not_denied",
    "production_bucket_denial_unproven", "production_bucket_access_not_denied",
    "invalid_batch_identity", "synthetic_source_roundtrip_mismatch",
    "compatible_frame_decode_failed", "compatible_upload_size_mismatch",
    "synthetic_objects_remain", "playback_export_codec_mismatch",
})


def verify_database_boundary(connection, production_database: str) -> None:
    # libpq connection-time FATAL errors may have no SQLSTATE. Query the same
    # authoritative permission used by the development/preprod release gates.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_database(), current_user, "
            "has_database_privilege(current_user, %s, 'CONNECT')",
            [production_database],
        )
        database, user, production_connect = cursor.fetchone()
    if (database, user) != ("academy_api_development", "academy_api_development_app"):
        raise RuntimeError("development_database_required")
    if production_connect is not False:
        raise RuntimeError("production_database_denial_unproven")


def failure_record(error: Exception) -> dict:
    # Only fixed guard codes can cross the log boundary, never SDK/DB messages.
    code = error.args[0] if type(error) is RuntimeError and len(error.args) == 1 else None
    return {"schema": "homework-preview-development-canary/v1", "success": False,
            "failure_type": type(error).__name__,
            "failure_code": code if isinstance(code, str) and code in FAILURE_CODES else "unexpected_error"}


def verify_playback_export(*, storage, bucket: str, prefix: str, directory: Path) -> None:
    from academy.adapters.video.playback_export import _remux_stored_rendition

    source = directory / "hls"
    variant = source / "v0"
    variant.mkdir(parents=True)
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3",
        "-c:v", "libx264", "-threads", "2", "-g", "15", "-c:a", "aac",
        "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(variant / "index%d.ts"), str(variant / "index.m3u8"),
    ], check=True, capture_output=True, timeout=30)
    master = source / "master.m3u8"
    master.write_text("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000\nv0/index.m3u8\n", encoding="utf-8")
    output = directory / "playback.mp4"
    keys = []
    files = sorted(variant.iterdir())
    if any(not re.fullmatch(r"index(?:\.m3u8|[0-7]\.ts)", file.name) for file in files):
        raise RuntimeError("playback_export_codec_mismatch")
    try:
        for file in [master, *files]:
            key = prefix + "hls/" + file.relative_to(source).as_posix()
            keys.append(key)
            storage.put_object(Bucket=bucket, Key=key, Body=file.read_bytes())
        _remux_stored_rendition(client=storage, bucket=bucket, master_key=keys[0], output=output)
        measured = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "json", str(output),
        ], check=True, capture_output=True, timeout=15)
        if {stream["codec_name"] for stream in json.loads(measured.stdout)["streams"]} != {"h264", "aac"}:
            raise RuntimeError("playback_export_codec_mismatch")
        keys.append(prefix + "playback.mp4")
        storage.put_object(Bucket=bucket, Key=keys[-1], Body=output.read_bytes(), ContentType="video/mp4",
                           CacheControl="private, no-store")
        stored = storage.head_object(Bucket=bucket, Key=keys[-1])
        if stored["ContentLength"] != output.stat().st_size or stored["ContentType"] != "video/mp4":
            raise RuntimeError("compatible_upload_size_mismatch")
    finally:
        for key in keys:
            storage.delete_object(Bucket=bucket, Key=key)


def run(*, release_id: str, production_database: str) -> dict:
    from django.conf import settings
    from django.db import connections
    from botocore.exceptions import ClientError
    import boto3

    from academy.adapters.storage.r2_objects import video_bucket_client
    from apps.infrastructure.storage.r2 import _get_s3_client
    from apps.worker.video_worker.homework_preview_main import convert_video, probe_video

    if not os.environ.get("AWS_BATCH_JOB_ID") or os.environ.get("ACADEMY_RUNTIME_ENV") != "development":
        raise RuntimeError("development_batch_required")
    if os.environ.get("ACADEMY_DEVELOPMENT_RELEASE_ID") != release_id:
        raise RuntimeError("candidate_environment_mismatch")
    if settings.R2_AI_BUCKET != "academy-development-artifacts" or settings.R2_VIDEO_BUCKET != settings.R2_AI_BUCKET:
        raise RuntimeError("development_bucket_required")
    if settings.VIDEO_BATCH_JOB_QUEUE or settings.VIDEO_BATCH_JOB_DEFINITION:
        raise RuntimeError("general_video_must_remain_disabled")
    verify_database_boundary(connections["default"], production_database)

    ssm = boto3.client("ssm", region_name=os.environ["AWS_DEFAULT_REGION"])
    try:
        ssm.get_parameter(Name="/academy/workers/env", WithDecryption=False)
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"AccessDenied", "AccessDeniedException"}:
            raise RuntimeError("production_parameter_denial_unproven") from None
    else:
        raise RuntimeError("production_parameter_access_not_denied")

    storage = _get_s3_client(timeout_seconds=15)
    try:
        storage.head_bucket(Bucket="academy-ai")
    except ClientError as error:
        # R2 may conceal a forbidden bucket with 404, as in the owning
        # development runtime gate. Transport/authentication errors still fail.
        if str(error.response["Error"]["Code"]) not in {"403", "404", "AccessDenied", "NoSuchBucket"}:
            raise RuntimeError("production_bucket_denial_unproven") from None
    else:
        raise RuntimeError("production_bucket_access_not_denied")

    job_id = os.environ["AWS_BATCH_JOB_ID"]
    if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
        raise RuntimeError("invalid_batch_identity")
    # Deterministic owned prefix lets the orchestrator clean up even after SIGKILL.
    prefix = "qa-homework-preview/" + job_id + "/"
    keys = [prefix + "source.mp4", prefix + "preview.mp4"]
    source = Path(__file__).parent / "fixtures" / "homework-hevc.mp4"
    result = {"schema": "homework-preview-development-canary/v1", "release_id": release_id,
              "development_database": True, "production_database_denied": True,
              "production_parameter_denied": True, "production_bucket_denied": True}
    try:
        storage.put_object(Bucket=settings.R2_AI_BUCKET, Key=keys[0], Body=source.read_bytes(), ContentType="video/mp4")
        response = storage.get_object(Bucket=settings.R2_AI_BUCKET, Key=keys[0])
        try:
            body = response["Body"].read(1024 * 1024)
        finally:
            response["Body"].close()
        if hashlib.sha256(body).digest() != hashlib.sha256(source.read_bytes()).digest():
            raise RuntimeError("synthetic_source_roundtrip_mismatch")
        with tempfile.TemporaryDirectory(prefix="homework-canary-") as directory:
            downloaded = Path(directory) / "source.mp4"
            output = Path(directory) / "preview.mp4"
            downloaded.write_bytes(body)
            convert_video(downloaded, output)
            probe = probe_video(output)
            frame = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(output),
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                capture_output=True, check=True, timeout=30).stdout
            if len(frame) != probe["width"] * probe["height"] * 3 or len(set(frame)) < 16:
                raise RuntimeError("compatible_frame_decode_failed")
            storage.put_object(Bucket=settings.R2_AI_BUCKET, Key=keys[1], Body=output.read_bytes(), ContentType="video/mp4")
            stored = storage.head_object(Bucket=settings.R2_AI_BUCKET, Key=keys[1])
            if stored["ContentLength"] != output.stat().st_size:
                raise RuntimeError("compatible_upload_size_mismatch")
            result.update(source_codec=probe_video(downloaded)["codec_name"], preview_codec=probe["codec_name"],
                          decoded_frame_sha256=hashlib.sha256(frame).hexdigest())
            verify_playback_export(storage=video_bucket_client(), bucket=settings.R2_VIDEO_BUCKET,
                                   prefix=prefix, directory=Path(directory))
            result["playback_export_codec_verified"] = True
    finally:
        for key in keys:
            storage.delete_object(Bucket=settings.R2_AI_BUCKET, Key=key)
        remaining = storage.list_objects_v2(Bucket=settings.R2_AI_BUCKET, Prefix=prefix).get("KeyCount", 0)
        if remaining:
            raise RuntimeError("synthetic_objects_remain")
        result["remaining_objects"] = 0
    return result


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    import django
    try:
        django.setup()
        result = run(release_id=sys.argv[1], production_database=sys.argv[2])
    except Exception as error:
        print(json.dumps(failure_record(error)))
        return 1
    print(json.dumps({**result, "success": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
