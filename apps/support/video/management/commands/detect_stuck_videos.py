# PATH: apps/support/video/management/commands/detect_stuck_videos.py
"""
Stuck video detector — 운영 신뢰성 모니터링.

탐지 대상:
1. UPLOADED 상태 + active job 없음 + 1시간 이상 방치
2. RETRY_WAIT 상태 job + 2시간 이상 체류
3. UPLOADED 상태 + RETRY_WAIT/FAILED job + 자동 복구 실패

--repair: 탐지된 영상을 자동 복구 (re-enqueue)
--dry-run: 로그만 출력

Run via cron (e.g. every 30 min):
  python manage.py detect_stuck_videos
  python manage.py detect_stuck_videos --repair
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from apps.support.video.models import Video, VideoTranscodeJob

logger = logging.getLogger(__name__)

UPLOADED_STALE_MINUTES = 60  # UPLOADED 상태 1시간 이상
RETRY_WAIT_STALE_MINUTES = 120  # RETRY_WAIT 2시간 이상
MAX_ATTEMPTS = 5


class Command(BaseCommand):
    help = "Detect and optionally repair stuck videos (UPLOADED without job, long RETRY_WAIT)"

    def add_arguments(self, parser):
        parser.add_argument("--repair", action="store_true", help="Auto-repair stuck videos")
        parser.add_argument("--dry-run", action="store_true", help="Only log, no changes")

    def handle(self, *args, **options):
        repair = options.get("repair", False)
        dry_run = options.get("dry_run", False)
        now = timezone.now()

        stuck_count = 0
        repaired_count = 0

        # --- 1. UPLOADED videos without active job ---
        uploaded_cutoff = now - timedelta(minutes=UPLOADED_STALE_MINUTES)
        active_video_ids = set(
            VideoTranscodeJob.objects.filter(
                state__in=[
                    VideoTranscodeJob.State.QUEUED,
                    VideoTranscodeJob.State.RUNNING,
                    VideoTranscodeJob.State.RETRY_WAIT,
                ],
            ).values_list("video_id", flat=True)
        )

        orphan_videos = (
            Video.objects.filter(
                status=Video.Status.UPLOADED,
                updated_at__lt=uploaded_cutoff,
            )
            .exclude(id__in=active_video_ids)
            .filter(file_key__gt="")  # has uploaded file
            .select_related("session__lecture__tenant")
        )

        for v in orphan_videos:
            tenant_id = v.session.lecture.tenant_id if v.session and v.session.lecture else "?"
            age_hours = (now - v.updated_at).total_seconds() / 3600
            self.stdout.write(
                self.style.WARNING(
                    f"STUCK_ORPHAN | video_id={v.id} tenant={tenant_id} "
                    f'title="{v.title}" age={age_hours:.1f}h status={v.status}'
                )
            )
            stuck_count += 1

            if repair and not dry_run:
                try:
                    from apps.support.video.services.video_encoding import create_job_and_submit_batch
                    from apps.support.video.services import video_job_lock

                    video_job_lock.release(v.id)
                    result = create_job_and_submit_batch(v)
                    if result.job:
                        self.stdout.write(
                            self.style.SUCCESS(f"  REPAIRED | video_id={v.id} new_job={result.job.id}")
                        )
                        repaired_count += 1
                    else:
                        self.stderr.write(f"  REPAIR_FAILED | video_id={v.id} reason={result.reject_reason}")
                except Exception as e:
                    self.stderr.write(f"  REPAIR_ERROR | video_id={v.id} error={e}")

        # --- 2. Long RETRY_WAIT jobs ---
        retry_cutoff = now - timedelta(minutes=RETRY_WAIT_STALE_MINUTES)
        stale_retry_jobs = VideoTranscodeJob.objects.filter(
            state=VideoTranscodeJob.State.RETRY_WAIT,
            updated_at__lt=retry_cutoff,
        ).select_related("video")

        for job in stale_retry_jobs:
            age_hours = (now - job.updated_at).total_seconds() / 3600
            self.stdout.write(
                self.style.WARNING(
                    f"STUCK_RETRY_WAIT | job_id={job.id} video_id={job.video_id} "
                    f"attempts={job.attempt_count} age={age_hours:.1f}h"
                )
            )
            stuck_count += 1

            if repair and not dry_run:
                from academy.adapters.db.django.repositories_video import job_mark_dead

                if job.attempt_count >= MAX_ATTEMPTS:
                    job_mark_dead(
                        str(job.id),
                        error_code="STUCK_MAX_ATTEMPTS",
                        error_message=f"RETRY_WAIT {age_hours:.1f}h, attempts={job.attempt_count}",
                    )
                    self.stdout.write(self.style.WARNING(f"  DEAD | job_id={job.id} (max attempts)"))
                else:
                    # Resubmit to Batch
                    try:
                        from apps.support.video.services.batch_submit import submit_batch_job
                        from apps.support.video.services import video_job_lock

                        video_job_lock.release(job.video_id)
                        dur = int(job.video.duration) if job.video and job.video.duration else None
                        aws_job_id, err = submit_batch_job(str(job.id), duration_seconds=dur)
                        if aws_job_id:
                            job.state = VideoTranscodeJob.State.QUEUED
                            job.aws_batch_job_id = aws_job_id
                            job.save(update_fields=["state", "aws_batch_job_id", "updated_at"])
                            self.stdout.write(
                                self.style.SUCCESS(f"  RESUBMITTED | job_id={job.id} batch={aws_job_id[:12]}")
                            )
                            repaired_count += 1
                        else:
                            self.stderr.write(f"  RESUBMIT_FAILED | job_id={job.id} error={err}")
                    except Exception as e:
                        self.stderr.write(f"  RESUBMIT_ERROR | job_id={job.id} error={e}")

        # --- 3. Summary ---
        try:
            from apps.support.video.services.ops_events import emit_ops_event

            if stuck_count > 0:
                emit_ops_event(
                    "VIDEO_STUCK_DETECTED",
                    severity="WARNING",
                    payload={
                        "stuck_count": stuck_count,
                        "repaired_count": repaired_count,
                    },
                )
        except Exception:
            pass

        if stuck_count == 0:
            self.stdout.write(self.style.SUCCESS("No stuck videos detected."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Total: stuck={stuck_count} repaired={repaired_count}"
                    + (" (dry-run)" if dry_run else "")
                )
            )
