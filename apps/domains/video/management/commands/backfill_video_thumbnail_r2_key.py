"""
Backfill Video.thumbnail_r2_key for legacy READY videos.

Pre-fix: Worker uploaded thumbnail.jpg to R2 but never wrote the path to DB,
so 모든 살아있는 영상이 thumbnail 빈 상태 → 모바일 카드 placeholder. Post-fix
(0015 + processor + job_complete contract) 신규 영상은 자동으로 채워지므로
이 커맨드는 **1회성 (재발 방지는 코드 invariant 가 담당)**.

전략: Video 마다 R2에 head_object 로 thumbnail.jpg 존재 확인. 있으면 DB
update, 없으면 missing 리스트로 분리 보고 (재인코딩 후보).

--dry-run: 변경 없이 카운트만.
--include-deleted: soft-deleted 영상도 포함 (default 제외).
"""
from __future__ import annotations

import logging

import boto3
from botocore.config import Config as BotoConfig
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.r2_paths import video_hls_prefix
from apps.domains.video.models import Video

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill Video.thumbnail_r2_key from R2 (1회성, 0015 migration 직후 실행)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--include-deleted", action="store_true")
        parser.add_argument("--tenant-id", type=int, default=None)

    def handle(self, *args, **opts):
        dry = bool(opts.get("dry_run"))
        include_deleted = bool(opts.get("include_deleted"))
        tenant_id = opts.get("tenant_id")

        manager = Video.all_with_deleted if include_deleted else Video.objects
        qs = manager.all()
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        qs = qs.order_by("tenant_id", "id")

        client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
            region_name="auto",
            config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
        )
        bucket = settings.R2_VIDEO_BUCKET

        total = qs.count()
        already = 0
        backfilled = 0
        missing = []
        skipped_no_tenant = 0

        for v in qs.iterator(chunk_size=200):
            if not v.tenant_id:
                skipped_no_tenant += 1
                continue
            if (v.thumbnail_r2_key or "").strip():
                already += 1
                continue
            key = f"{video_hls_prefix(tenant_id=v.tenant_id, video_id=v.id).rstrip('/')}/thumbnail.jpg"
            try:
                client.head_object(Bucket=bucket, Key=key)
            except Exception:
                missing.append((v.tenant_id, v.id, v.title))
                continue
            if dry:
                backfilled += 1
                continue
            Video.all_with_deleted.filter(pk=v.pk).update(thumbnail_r2_key=key)
            backfilled += 1

        self.stdout.write(self.style.SUCCESS(
            f"total={total} already_set={already} backfilled={backfilled} "
            f"missing_in_r2={len(missing)} skipped_no_tenant={skipped_no_tenant} dry_run={dry}"
        ))
        if missing:
            self.stdout.write("missing thumbnail in R2 (reencode candidates):")
            for t, i, title in missing[:50]:
                self.stdout.write(f"  tenant={t} video={i} title={title!r}")
            if len(missing) > 50:
                self.stdout.write(f"  ... and {len(missing) - 50} more")
