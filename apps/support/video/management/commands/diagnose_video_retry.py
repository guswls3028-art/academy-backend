# PATH: apps/support/video/management/commands/diagnose_video_retry.py
"""
진단: retry 실패 원인 확인용.
지정한 video_id들의 status, file_key, current_job_id, R2 원본 객체 존재 여부를 출력.
프로덕션에서 409/400 원인 확인 시 사용: python manage.py diagnose_video_retry 219 215
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.support.video.models import Video, VideoTranscodeJob


def _head_object(key: str):
    from libs.s3_client.client import head_object
    return head_object(key)


class Command(BaseCommand):
    help = "Diagnose video retry: print status, file_key, current_job, R2 raw object existence for given video IDs"

    def add_arguments(self, parser):
        parser.add_argument(
            "video_ids",
            nargs="+",
            type=int,
            help="Video IDs to diagnose (e.g. 219 215)",
        )

    def handle(self, *args, **options):
        video_ids = options["video_ids"]
        for vid in video_ids:
            self._diagnose_one(vid)

    def _diagnose_one(self, video_id: int):
        video = Video.objects.filter(pk=video_id).select_related(
            "session", "session__lecture", "session__lecture__tenant"
        ).first()
        if not video:
            self.stdout.write(f"video_id={video_id} NOT_FOUND")
            return
        status = getattr(video, "status", None)
        file_key = (getattr(video, "file_key", None) or "").strip()
        current_job_id = getattr(video, "current_job_id", None)
        tenant_id = None
        if video.session and video.session.lecture:
            tenant_id = video.session.lecture.tenant_id

        self.stdout.write(
            f"video_id={video_id} status={status} file_key_len={len(file_key)} "
            f"current_job_id={current_job_id} tenant_id={tenant_id}"
        )
        if file_key:
            self.stdout.write(f"  file_key={file_key[:100]}{'...' if len(file_key) > 100 else ''}")
        else:
            self.stdout.write("  file_key=(empty) -> retry returns 400 '업로드가 완료되지 않았습니다' or '파일 정보가 없습니다'")

        if current_job_id:
            job = VideoTranscodeJob.objects.filter(pk=current_job_id).first()
            if job:
                self.stdout.write(
                    f"  job_id={job.id} state={job.state} aws_batch_job_id={getattr(job, 'aws_batch_job_id', None) or '(none)'}"
                )
            else:
                self.stdout.write(f"  current_job_id={current_job_id} (job row not found)")

        if file_key:
            try:
                exists, size = _head_object(file_key)
                self.stdout.write(f"  R2 head_object: exists={exists} size={size}")
                if not exists or size == 0:
                    self.stdout.write(
                        "  -> retry (PENDING or re-enqueue) returns 409 'S3 object not found' / 업로드된 파일을 찾을 수 없습니다"
                    )
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  R2 head_object error: {e}"))
