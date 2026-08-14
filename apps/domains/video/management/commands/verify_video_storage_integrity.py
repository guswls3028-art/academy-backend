"""
Optional post-READY validation: iterate READY videos, check master.m3u8 exists, at least N segments.
Report corrupted prefixes. No automatic repair.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from academy.adapters.storage.r2_objects import (
    count_r2_objects_with_suffix_or_raise,
    r2_head_exists_or_raise,
)
from apps.domains.video.models import Video


def _head_exists(bucket: str, key: str) -> bool:
    return r2_head_exists_or_raise(bucket=bucket, key=key)


def _count_segments(bucket: str, prefix: str, max_keys: int = 500) -> int:
    return count_r2_objects_with_suffix_or_raise(
        bucket=bucket,
        prefix=prefix,
        suffix=".ts",
        max_keys=max_keys,
    )


class Command(BaseCommand):
    help = "Verify READY videos: master.m3u8 exists, at least N segments; report corrupted prefixes"

    def add_arguments(self, parser):
        parser.add_argument("--min-segments", type=int, default=1, help="Minimum .ts segments required (default 1)")
        parser.add_argument(
            "--after-id",
            type=int,
            default=0,
            help="Check READY videos with id greater than this cursor",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum videos in this batch (0 = unlimited)",
        )

    def handle(self, *args, **options):
        min_segments = options["min_segments"]
        after_id = int(options["after_id"])
        limit = int(options["limit"])
        if min_segments < 1 or after_id < 0 or limit < 0:
            raise CommandError("min-segments must be >=1; after-id and limit must be >=0")
        bucket = getattr(settings, "R2_VIDEO_BUCKET", None)
        if not bucket:
            raise CommandError("R2_VIDEO_BUCKET not set")

        ready_qs = (
            Video.objects.filter(status=Video.Status.READY, id__gt=after_id)
            .exclude(hls_path="")
            .select_related("session__lecture")
            .order_by("id")
        )
        if limit:
            ready_rows = list(ready_qs[: limit + 1])
            has_more = len(ready_rows) > limit
            ready_rows = ready_rows[:limit]
        else:
            ready_rows = list(ready_qs)
            has_more = False
        corrupted = []
        ok_count = 0
        for video in ready_rows:
            hls_path = (video.hls_path or "").strip()
            if not hls_path:
                continue
            try:
                if not _head_exists(bucket, hls_path):
                    corrupted.append({"video_id": video.id, "reason": "master.m3u8 missing", "prefix": hls_path.rsplit("/", 1)[0] + "/"})
                    continue
                prefix = hls_path.rsplit("/", 1)[0] + "/"
                seg_count = _count_segments(bucket, prefix)
            except Exception as exc:
                raise CommandError(
                    f"R2 verification failed for video_id={video.id}: "
                    f"{type(exc).__name__}"
                ) from exc
            if seg_count < min_segments:
                corrupted.append({"video_id": video.id, "reason": f"segments={seg_count} < {min_segments}", "prefix": prefix})
                continue
            ok_count += 1

        if corrupted:
            self.stdout.write(self.style.WARNING(f"Corrupted or incomplete: {len(corrupted)}"))
            for c in corrupted[:100]:
                self.stdout.write(f"  video_id={c['video_id']} {c['reason']} prefix={c['prefix']}")
            if len(corrupted) > 100:
                self.stdout.write(f"  ... and {len(corrupted) - 100} more")
        last_id = ready_rows[-1].id if ready_rows else after_id
        self.stdout.write(
            f"BATCH: checked={len(ready_rows)} after_id={after_id} "
            f"last_id={last_id} has_more={str(has_more).lower()}"
        )
        self.stdout.write(self.style.SUCCESS(f"OK: {ok_count} READY videos verified"))
        if corrupted:
            raise CommandError(
                f"video_storage_integrity_failed:corrupted={len(corrupted)}"
            )
