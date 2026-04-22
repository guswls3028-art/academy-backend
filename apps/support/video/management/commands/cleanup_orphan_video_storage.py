# PATH: apps/support/video/management/commands/cleanup_orphan_video_storage.py
"""
R2 video 버킷에서 DB와 매칭되지 않는 orphan 파일 정리.

대상 카테고리:
  1. RAW orphan     — tenants/*/video/raw/**/<uuid>.<ext> 중 어떤 Video.file_key 에도 매칭되지 않는 원본.
                     (Video 행 hard-delete 후 R2 정리 실패 / complete 콜백 실패로 Video row 미생성)
  2. HLS orphan     — tenants/*/video/hls/<video_id>/ 의 {video_id} 가 Video.all_with_deleted 에 없거나
                     존재하더라도 hls_path 가 비어 있는(=인코딩 실패) 경우.
  3. _tmp orphan    — hls/<vid>/_tmp/<job_id>/ 아래 파일. 규약상 임시 → 일정 시간 경과 시 전부 orphan.
  4. STALE PENDING  — Video.status=PENDING 이면서 R2 raw 가 존재하지 않고, created_at 이 cutoff 이전.

안전장치:
  - 기본 dry-run. --apply 필요.
  - R2 LastModified 가 --min-age-hours 이상일 때만 삭제 (업로드/인코딩 race 회피).
  - PENDING 행 정리는 soft-delete(deleted_at 설정). DB row는 보존.
  - R2_VIDEO_BUCKET 미설정 시 즉시 abort.

크론 예:
  python manage.py cleanup_orphan_video_storage --apply
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

HLS_KEY_PAT = re.compile(r"^(tenants/[^/]+/video/hls/[^/]+/)")


class Command(BaseCommand):
    help = "Remove orphan raw/HLS/_tmp files in R2 video bucket and stale PENDING Video rows."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually delete. Without this flag only a report is emitted.")
        parser.add_argument("--min-age-hours", type=int, default=48,
                            help="Minimum R2 LastModified age in hours before an object is eligible (default 48).")
        parser.add_argument("--pending-age-hours", type=int, default=24,
                            help="Minimum Video.created_at age in hours for stale PENDING cleanup (default 24).")
        parser.add_argument("--include-raw", action="store_true", default=True)
        parser.add_argument("--include-hls", action="store_true", default=True)
        parser.add_argument("--include-tmp", action="store_true", default=True)
        parser.add_argument("--include-pending", action="store_true", default=True)
        parser.add_argument("--skip-raw", action="store_true")
        parser.add_argument("--skip-hls", action="store_true")
        parser.add_argument("--skip-tmp", action="store_true")
        parser.add_argument("--skip-pending", action="store_true")

    def handle(self, *args, **opts):
        apply = opts["apply"]
        min_age = timedelta(hours=opts["min_age_hours"])
        pending_age = timedelta(hours=opts["pending_age_hours"])
        do_raw = opts["include_raw"] and not opts["skip_raw"]
        do_hls = opts["include_hls"] and not opts["skip_hls"]
        do_tmp = opts["include_tmp"] and not opts["skip_tmp"]
        do_pending = opts["include_pending"] and not opts["skip_pending"]

        bucket = getattr(settings, "R2_VIDEO_BUCKET", None)
        if not bucket:
            raise CommandError("R2_VIDEO_BUCKET not configured")

        import boto3
        s3 = boto3.client(
            "s3",
            region_name="auto",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
        )

        from apps.support.video.models import Video

        db_raw_keys = set(
            Video.all_with_deleted.exclude(file_key="").values_list("file_key", flat=True)
        )
        valid_hls_roots: set[str] = set()
        for hp in Video.all_with_deleted.exclude(hls_path="").values_list("hls_path", flat=True):
            if hp:
                valid_hls_roots.add(hp.rsplit("/", 1)[0] + "/")

        now = timezone.now()
        raw_orphans: list[tuple[str, int]] = []
        hls_orphans_by_root: dict[str, list[tuple[str, int]]] = {}
        tmp_orphans: list[tuple[str, int]] = []
        too_young = 0

        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix="tenants/"):
            for obj in page.get("Contents") or []:
                k = obj.get("Key") or ""
                sz = int(obj.get("Size") or 0)
                lm = obj.get("LastModified")
                if lm is not None and (now - lm) < min_age:
                    too_young += 1
                    continue

                if "/video/raw/" in k:
                    if k in db_raw_keys:
                        continue
                    if do_raw:
                        raw_orphans.append((k, sz))
                    continue

                if "/video/hls/" not in k:
                    continue

                if "/_tmp/" in k:
                    if do_tmp:
                        tmp_orphans.append((k, sz))
                    continue

                m = HLS_KEY_PAT.match(k)
                if not m:
                    continue
                root = m.group(1)
                if root in valid_hls_roots:
                    continue
                if do_hls:
                    hls_orphans_by_root.setdefault(root, []).append((k, sz))

        pending_rows = []
        if do_pending:
            cutoff = now - pending_age
            pending_qs = Video.objects.filter(
                status=Video.Status.PENDING, created_at__lt=cutoff
            )
            for v in pending_qs:
                exists = False
                if v.file_key:
                    try:
                        s3.head_object(Bucket=bucket, Key=v.file_key)
                        exists = True
                    except Exception:
                        exists = False
                if not exists:
                    pending_rows.append(v)

        raw_bytes = sum(s for _, s in raw_orphans)
        tmp_bytes = sum(s for _, s in tmp_orphans)
        hls_bytes = sum(s for files in hls_orphans_by_root.values() for _, s in files)

        def gb(b: int) -> str:
            return f"{b / 1024 / 1024 / 1024:.2f} GB"

        self.stdout.write(self.style.HTTP_INFO("=== SCAN RESULT ==="))
        self.stdout.write(f"apply={apply} min_age_hours={opts['min_age_hours']} pending_age_hours={opts['pending_age_hours']}")
        self.stdout.write(f"objects skipped as too young: {too_young}")
        self.stdout.write(f"raw orphans: {len(raw_orphans)} ({gb(raw_bytes)})")
        self.stdout.write(f"hls orphan roots: {len(hls_orphans_by_root)} total files "
                          f"{sum(len(v) for v in hls_orphans_by_root.values())} ({gb(hls_bytes)})")
        self.stdout.write(f"tmp orphans: {len(tmp_orphans)} ({gb(tmp_bytes)})")
        self.stdout.write(f"stale PENDING rows (no R2): {len(pending_rows)}")

        for root, files in sorted(hls_orphans_by_root.items()):
            rb = sum(s for _, s in files)
            self.stdout.write(f"  HLS root={root} files={len(files)} MB={rb/1024/1024:.1f}")

        if not apply:
            self.stdout.write(self.style.WARNING("Dry-run (no changes). Re-run with --apply to execute."))
            return

        deleted_objects = 0
        # --- delete raw ---
        for i in range(0, len(raw_orphans), 1000):
            batch = raw_orphans[i:i + 1000]
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k, _ in batch], "Quiet": True},
            )
            deleted_objects += len(batch)
            logger.info("cleanup_orphan_video_storage: raw batch deleted count=%s", len(batch))

        # --- delete tmp ---
        for i in range(0, len(tmp_orphans), 1000):
            batch = tmp_orphans[i:i + 1000]
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k, _ in batch], "Quiet": True},
            )
            deleted_objects += len(batch)
            logger.info("cleanup_orphan_video_storage: tmp batch deleted count=%s", len(batch))

        # --- delete HLS per root (use delete_prefix for safety) ---
        from apps.infrastructure.storage.r2 import delete_prefix_r2_video
        for root in sorted(hls_orphans_by_root.keys()):
            n = delete_prefix_r2_video(prefix=root)
            deleted_objects += n
            logger.info("cleanup_orphan_video_storage: hls root=%s deleted=%s", root, n)
            self.stdout.write(f"  deleted HLS root={root} count={n}")

        # --- stale PENDING rows: soft-delete ---
        pending_soft_deleted = 0
        for v in pending_rows:
            v.status = Video.Status.FAILED
            v.error_reason = (v.error_reason or "") + "\n[cleanup] stale PENDING: R2 raw missing"
            v.deleted_at = now
            v.save(update_fields=["status", "error_reason", "deleted_at", "updated_at"])
            pending_soft_deleted += 1
            logger.info("cleanup_orphan_video_storage: PENDING soft-deleted video_id=%s", v.id)

        self.stdout.write(self.style.SUCCESS(
            f"Done. deleted_objects={deleted_objects} pending_soft_deleted={pending_soft_deleted}"
        ))
