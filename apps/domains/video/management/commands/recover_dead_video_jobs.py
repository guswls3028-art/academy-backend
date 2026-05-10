"""
DEAD VideoTranscodeJob 자동 회복.

5회 재시도 한도를 다 쓴 DEAD job 중 transient 사유(인프라/네트워크)로 죽은 것을
새 job 1번에 한해 자동 재시도. 진짜 컨텐츠 결함이면 다시 DEAD로 굳어 학원장이
admin UI에서 재업로드해야 함.

회복 대상:
- Job.state=DEAD AND error_code IN ('', 'MAX_ATTEMPTS', 'RECONCILE_MAX_ATTEMPTS', 'TIMEOUT')
- Job.created_at >= now - 7d (오래된 DEAD는 raw 파일 purge 됐을 가능성 — purge_raw_videos 3일 정책)
- Video.status='FAILED' (READY는 이미 다른 경로로 회복됨)
- Video.file_key 존재
- 같은 video에 더 새로운 SUCCEEDED job 없음 (이미 회복됨)
- 같은 video에 더 새로운 active(QUEUED/RUNNING/RETRY_WAIT) job 없음 (재시도 진행중)
- "auto_recovered" payload 없는 DEAD만 (loop 방지 — 한 번만 자동 회복)

재제출 흐름:
- video.status=UPLOADED 복구 (FAILED→UPLOADED), error_reason 클리어
- create_job_and_submit_batch가 새 Job + DDB lock + Batch submit
- 새 Job은 attempt_count=1부터 시작

EventBridge cron 예시: rate(2 hours)
  python manage.py recover_dead_video_jobs
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.domains.video.models import Video, VideoTranscodeJob


# Transient infra/network 사유. 진짜 영상 corruption은 제외.
RETRYABLE_ERROR_CODES = frozenset({
    "",
    "MAX_ATTEMPTS",
    "RECONCILE_MAX_ATTEMPTS",
    "TIMEOUT",
})

# DEAD 시점에 auto-recover 마킹할 키 (loop 방지)
RECOVER_PAYLOAD_KEY = "auto_recovered_at"


class Command(BaseCommand):
    help = "Recover DEAD VideoTranscodeJob with transient error codes (1 retry per video)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--max-age-days",
            type=int,
            default=7,
            help="DEAD jobs older than this are skipped (default: 7d, raw 파일 purge 3d 후로는 회복 불가)",
        )
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        from apps.domains.video.services.video_encoding import create_job_and_submit_batch

        dry_run = options["dry_run"]
        max_age_days = options["max_age_days"]
        limit = options["limit"]

        cutoff = timezone.now() - timedelta(days=max_age_days)

        active_states = (
            VideoTranscodeJob.State.QUEUED,
            VideoTranscodeJob.State.RUNNING,
            VideoTranscodeJob.State.RETRY_WAIT,
        )

        dead_jobs = (
            VideoTranscodeJob.objects
            .filter(
                state=VideoTranscodeJob.State.DEAD,
                error_code__in=RETRYABLE_ERROR_CODES,
                created_at__gte=cutoff,
            )
            .select_related("video")
            .order_by("-created_at")[:limit * 3]  # 후처리 필터링 여유분
        )

        recovered = 0
        skipped_already_recovered = 0
        skipped_video_state = 0
        skipped_active_exists = 0
        skipped_succeeded_exists = 0
        skipped_no_file = 0
        failed = 0

        for dead in dead_jobs:
            if recovered >= limit:
                break

            video = dead.video
            if not video:
                continue

            # 같은 video에 이미 SUCCEEDED job 있으면 skip (이미 회복됨)
            if VideoTranscodeJob.objects.filter(
                video_id=dead.video_id,
                state=VideoTranscodeJob.State.SUCCEEDED,
                created_at__gt=dead.created_at,
            ).exists():
                skipped_succeeded_exists += 1
                continue

            # 같은 video에 active job 있으면 skip (이미 진행중)
            if VideoTranscodeJob.objects.filter(
                video_id=dead.video_id,
                state__in=active_states,
            ).exists():
                skipped_active_exists += 1
                continue

            # 자기 자신이 이미 auto-recovered 마킹 됐는지 (loop 방지)
            try:
                if (dead.error_message or "").find(RECOVER_PAYLOAD_KEY) >= 0:
                    skipped_already_recovered += 1
                    continue
            except Exception:
                pass

            # Video status / file_key 검증
            if video.status not in (Video.Status.FAILED, Video.Status.UPLOADED):
                skipped_video_state += 1
                continue
            if not (video.file_key or "").strip():
                skipped_no_file += 1
                continue

            self.stdout.write(
                f"RECOVER candidate | job={dead.id} video={video.id} "
                f"err_code={dead.error_code or '<empty>'} created={dead.created_at}"
            )
            if dry_run:
                recovered += 1
                continue

            try:
                with transaction.atomic():
                    # video를 UPLOADED 상태로 복구 (FAILED → UPLOADED)
                    Video.objects.filter(pk=video.id).update(
                        status=Video.Status.UPLOADED,
                        error_reason="",
                    )
                    # DEAD job에 회복 마킹 (loop 방지)
                    marker = (dead.error_message or "")
                    if RECOVER_PAYLOAD_KEY not in marker:
                        marker = (marker + f" [{RECOVER_PAYLOAD_KEY}={timezone.now().isoformat()}]")[:2000]
                        VideoTranscodeJob.objects.filter(pk=dead.id).update(error_message=marker)

                # video 객체 refresh
                video = Video.objects.get(pk=video.id)
                result = create_job_and_submit_batch(video)
                if result.job:
                    self.stdout.write(self.style.SUCCESS(
                        f"  ✅ recovered video={video.id} new_job={result.job.id}"
                    ))
                    recovered += 1
                else:
                    self.stderr.write(
                        f"  ❌ create_job_and_submit_batch failed video={video.id} reason={result.reject_reason}"
                    )
                    failed += 1
            except Exception as e:
                self.stderr.write(f"  ❌ exception video={video.id}: {e}")
                failed += 1

        self.stdout.write(
            f"\nSummary: recovered={recovered} skipped(succeeded_exists)={skipped_succeeded_exists} "
            f"skipped(active_exists)={skipped_active_exists} skipped(already_recovered)={skipped_already_recovered} "
            f"skipped(video_state)={skipped_video_state} skipped(no_file)={skipped_no_file} failed={failed}"
        )
