"""
Optional post-READY validation: iterate READY videos, check master.m3u8 exists, at least N segments.
Report corrupted prefixes. No automatic repair.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.conf import settings

from academy.adapters.storage.r2_objects import count_r2_objects_with_suffix, r2_head_exists
from apps.domains.video.models import Video


def _head_exists(bucket: str, key: str) -> bool:
    return r2_head_exists(bucket=bucket, key=key)


def _count_segments(bucket: str, prefix: str, max_keys: int = 500) -> int:
    return count_r2_objects_with_suffix(bucket=bucket, prefix=prefix, suffix=".ts", max_keys=max_keys)


class Command(BaseCommand):
    help = "Verify READY videos: master.m3u8 exists, at least N segments; report corrupted prefixes"

    def add_arguments(self, parser):
        parser.add_argument("--min-segments", type=int, default=1, help="Minimum .ts segments required (default 1)")

    def handle(self, *args, **options):
        min_segments = options["min_segments"]
        bucket = getattr(settings, "R2_VIDEO_BUCKET", None)
        if not bucket:
            self.stderr.write("R2_VIDEO_BUCKET not set")
            return

        ready = Video.objects.filter(status=Video.Status.READY).exclude(hls_path="").select_related("session__lecture")
        corrupted = []
        ok_count = 0
        for video in ready:
            hls_path = (video.hls_path or "").strip()
            if not hls_path:
                continue
            if not _head_exists(bucket, hls_path):
                corrupted.append({"video_id": video.id, "reason": "master.m3u8 missing", "prefix": hls_path.rsplit("/", 1)[0] + "/"})
                continue
            prefix = hls_path.rsplit("/", 1)[0] + "/"
            seg_count = _count_segments(bucket, prefix)
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
        self.stdout.write(self.style.SUCCESS(f"OK: {ok_count} READY videos verified"))
