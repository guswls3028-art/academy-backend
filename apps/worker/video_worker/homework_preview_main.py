"""AWS Batch-only conversion of a private homework video to browser MP4."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def probe_video(path: Path) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height:format=duration",
        "-of", "json", str(path),
    ], check=True, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    probe = json.loads(result.stdout)
    stream = (probe.get("streams") or [{}])[0]
    duration = float(probe.get("format", {}).get("duration") or 0)
    if not stream.get("width") or not stream.get("height") or not math.isfinite(duration) or duration <= 0:
        raise ValueError("video_has_no_decodable_frames")
    return {**stream, "duration": duration}


def convert_video(source: Path, output: Path) -> None:
    original = probe_video(source)
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
        "-protocol_whitelist", "file,pipe", "-threads", "2", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
        "-vf", "scale=w='min(1920,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart", "-map_metadata", "-1", "-fs", str(1024 * 1024 * 1024), str(output),
    ], check=True, capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
    converted = probe_video(output)
    if converted["codec_name"] != "h264" or abs(original["duration"] - converted["duration"]) > max(0.5, original["duration"] * 0.005):
        raise ValueError("incomplete_compatible_video")


def process_preview(preview_id: str, attempt_token: str) -> bool:
    if not os.environ.get("AWS_BATCH_JOB_ID"):
        raise RuntimeError("Homework video conversion requires AWS Batch")
    from django.db import transaction
    from django.utils import timezone
    from django.conf import settings
    from apps.domains.submissions.models import HomeworkMediaPreview
    from apps.domains.submissions.services.homework_preview import source_is_current
    from apps.domains.submissions.services.lifecycle import ensure_ai_submission_key_attachable, schedule_unreferenced_ai_object_cleanup
    from apps.infrastructure.storage.r2 import _get_s3_client, upload_fileobj_to_r2

    with transaction.atomic():
        preview = HomeworkMediaPreview.objects.select_for_update().select_related("submission").filter(
            pk=preview_id, attempt_token=attempt_token, status=HomeworkMediaPreview.Status.QUEUED,
        ).first()
        if preview is None:
            return False
        if not source_is_current(preview):
            preview.status = HomeworkMediaPreview.Status.FAILED
            preview.error_code = "source_changed"
            preview.save(update_fields=["status", "error_code", "updated_at"])
            return False
        ensure_ai_submission_key_attachable(tenant_id=preview.tenant_id, key=preview.object_key)
        preview.status = HomeworkMediaPreview.Status.PROCESSING
        preview.save(update_fields=["status", "updated_at"])
    object_written = False
    success = False
    try:
        with tempfile.TemporaryDirectory(prefix="homework-preview-") as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "preview.mp4"
            response = _get_s3_client(timeout_seconds=30).get_object(Bucket=settings.R2_AI_BUCKET, Key=preview.source_key)
            body = response["Body"]
            try:
                limit = 100 * 1024 * 1024
                if not 0 < int(response.get("ContentLength", 0)) <= limit:
                    raise ValueError("source_size_invalid")
                received = 0
                with source.open("wb") as target:
                    while chunk := body.read(1024 * 1024):
                        received += len(chunk)
                        if received > limit:
                            raise ValueError("source_size_invalid")
                        target.write(chunk)
            finally:
                body.close()
            convert_video(source, output)
            # An uncertain upload still needs durable compensation.
            object_written = True
            with output.open("rb") as content:
                upload_fileobj_to_r2(fileobj=content, key=preview.object_key, content_type="video/mp4", timeout_seconds=30)
            with transaction.atomic():
                current = HomeworkMediaPreview.objects.select_for_update().select_related("submission").filter(
                    pk=preview.pk, attempt_token=attempt_token, status=HomeworkMediaPreview.Status.PROCESSING,
                ).first()
                if current and source_is_current(current):
                    ensure_ai_submission_key_attachable(tenant_id=current.tenant_id, key=current.object_key)
                    current.status = HomeworkMediaPreview.Status.READY
                    current.error_code = ""
                    current.save(update_fields=["status", "error_code", "updated_at"])
                    success = True
    except Exception:
        # Neither source paths, credentials nor ffmpeg's raw stderr enter logs/API.
        HomeworkMediaPreview.objects.filter(pk=preview.pk, attempt_token=attempt_token).update(
            status=HomeworkMediaPreview.Status.FAILED, error_code="conversion_failed", updated_at=timezone.now(),
        )
    finally:
        if not success:
            HomeworkMediaPreview.objects.filter(pk=preview.pk, attempt_token=attempt_token).update(
                status=HomeworkMediaPreview.Status.FAILED, object_key="", updated_at=timezone.now(),
            )
            if object_written:
                schedule_unreferenced_ai_object_cleanup(tenant_id=preview.tenant_id, key=preview.object_key)
    return success


def main() -> int:
    if len(sys.argv) != 3 or not os.environ.get("DJANGO_SETTINGS_MODULE") or not os.environ.get("AWS_BATCH_JOB_ID"):
        return 2
    preview_id, attempt_token = (str(uuid.UUID(value)) for value in sys.argv[1:])
    import django
    django.setup()
    return 0 if process_preview(preview_id, attempt_token) else 1


if __name__ == "__main__":
    raise SystemExit(main())
