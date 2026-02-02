# PATH: apps/support/video/services/queue.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.support.video.models import Video


@dataclass(frozen=True)
class ClaimResult:
    video: Video
    leased_until: timezone.datetime


class VideoJobQueue:
    """
    ✅ SSOT (DB 기반 Video Worker Queue)

    철학:
    - DB가 단일 진실
    - claim은 원자적(select_for_update + skip_locked)
    - stuck reclaim은 "updated_at" 기반(기존 철학 유지) + lease 필드로 중앙 통제 보강
    - 멱등/경합 방지: leased_by/leased_until owner만 complete/fail/heartbeat 허용
    """

    @classmethod
    def _lease_seconds(cls) -> int:
        # settings 의존을 queue 레벨에서 강제하지 않기 위해 model 필드 기반으로만 설계
        # (settings가 필요하면 View 레벨에서 전달)
        return 60

    @classmethod
    def _reclaim_cutoff(cls, now) -> timezone.datetime:
        # PROCESSING 상태가 오래 갱신이 없으면 reclaim 가능
        return now - timedelta(seconds=cls._lease_seconds())

    @classmethod
    @transaction.atomic
    def claim_next(
        cls,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        max_batch: int = 1,
    ) -> Optional[Video]:
        """
        처리 대기 Video 1개를 claim 한다.
        - 반환: Video | None
        """
        wid = (worker_id or "").strip() or "worker-unknown"
        now = timezone.now()
        cutoff = now - timedelta(seconds=int(lease_seconds or 60))

        # 1) UPLOADED 우선
        # 2) PROCESSING 이면서 오래된(updated_at < cutoff) 작업 reclaim 허용
        video = (
            Video.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=Video.Status.UPLOADED)
                | Q(status=Video.Status.PROCESSING, updated_at__lt=cutoff)
            )
            .order_by("id")
            .first()
        )

        if not video:
            return None

        video.status = Video.Status.PROCESSING

        # lease 필드가 존재하면 반드시 기록 (모델에 이미 존재)
        if hasattr(video, "processing_started_at"):
            video.processing_started_at = now
        if hasattr(video, "leased_until"):
            video.leased_until = now + timedelta(seconds=int(lease_seconds or 60))
        if hasattr(video, "leased_by"):
            video.leased_by = wid

        update_fields = ["status"]
        if hasattr(video, "processing_started_at"):
            update_fields.append("processing_started_at")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")

        video.save(update_fields=update_fields)
        return video

    @classmethod
    @transaction.atomic
    def heartbeat(
        cls,
        *,
        video_id: int,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> bool:
        """
        lease 연장.
        - owner mismatch면 False
        """
        wid = (worker_id or "").strip() or "worker-unknown"
        now = timezone.now()

        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False

        # owner check (lease가 없으면 통과)
        leased_by = (getattr(video, "leased_by", "") or "").strip()
        leased_until = getattr(video, "leased_until", None)

        if leased_by and leased_by != wid:
            return False
        if leased_until and leased_until < now:
            return False

        if hasattr(video, "processing_started_at") and not video.processing_started_at:
            video.processing_started_at = now

        if hasattr(video, "leased_by"):
            video.leased_by = wid
        if hasattr(video, "leased_until"):
            video.leased_until = now + timedelta(seconds=int(lease_seconds or 60))

        update_fields = []
        if hasattr(video, "processing_started_at"):
            update_fields.append("processing_started_at")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")

        if update_fields:
            video.save(update_fields=update_fields)
        return True

    @classmethod
    @transaction.atomic
    def complete(
        cls,
        *,
        video_id: int,
        worker_id: str,
        hls_path: str,
        duration: int | None = None,
    ) -> tuple[bool, str]:
        """
        완료 처리.
        - owner mismatch면 (False, "lease_owner_mismatch")
        - 이미 READY + hls_path면 멱등 OK
        """
        wid = (worker_id or "").strip() or "worker-unknown"

        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False, "not_found"

        leased_by = (getattr(video, "leased_by", "") or "").strip()
        leased_until = getattr(video, "leased_until", None)
        now = timezone.now()

        if leased_by and leased_by != wid and (not leased_until or leased_until > now):
            return False, "lease_owner_mismatch"

        if video.status == Video.Status.READY and bool(video.hls_path):
            return True, "idempotent"

        video.hls_path = str(hls_path)

        if duration is not None and int(duration) >= 0:
            video.duration = int(duration)

        video.status = Video.Status.READY

        # lease 해제
        if hasattr(video, "leased_until"):
            video.leased_until = None
        if hasattr(video, "leased_by"):
            video.leased_by = ""

        update_fields = ["hls_path", "status"]
        if duration is not None and int(duration) >= 0:
            update_fields.append("duration")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")

        video.save(update_fields=update_fields)
        return True, "ok"

    @classmethod
    @transaction.atomic
    def fail(
        cls,
        *,
        video_id: int,
        worker_id: str,
        reason: str = "unknown",
    ) -> tuple[bool, str]:
        """
        실패 처리.
        - owner mismatch면 (False, "lease_owner_mismatch")
        - 이미 FAILED면 멱등 OK
        """
        wid = (worker_id or "").strip() or "worker-unknown"

        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False, "not_found"

        leased_by = (getattr(video, "leased_by", "") or "").strip()
        leased_until = getattr(video, "leased_until", None)
        now = timezone.now()

        if leased_by and leased_by != wid and (not leased_until or leased_until > now):
            return False, "lease_owner_mismatch"

        if video.status == Video.Status.FAILED:
            return True, "idempotent"

        video.status = Video.Status.FAILED
        if hasattr(video, "error_reason"):
            video.error_reason = str(reason)[:2000]

        # lease 해제
        if hasattr(video, "leased_until"):
            video.leased_until = None
        if hasattr(video, "leased_by"):
            video.leased_by = ""

        update_fields = ["status"]
        if hasattr(video, "error_reason"):
            update_fields.append("error_reason")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")

        video.save(update_fields=update_fields)
        return True, "ok"
