from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment


# ========================================================
# Access Mode (Video Access Policy)
# ========================================================

class AccessMode(models.TextChoices):
    """
    Video access mode enum.
    
    - FREE_REVIEW: Free review mode (no restrictions)
    - PROCTORED_CLASS: Proctored class mode (restrictions apply)
    - BLOCKED: Access blocked
    """
    FREE_REVIEW = "FREE_REVIEW", "복습"
    PROCTORED_CLASS = "PROCTORED_CLASS", "온라인 수업 대체"
    BLOCKED = "BLOCKED", "제한"


# ========================================================
# Video (영상 메타데이터)
# ========================================================

class Video(TimestampModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "업로드 대기"
        UPLOADED = "UPLOADED", "업로드 완료"
        PROCESSING = "PROCESSING", "처리중"
        READY = "READY", "사용 가능"
        FAILED = "FAILED", "실패"

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="videos",
    )

    title = models.CharField(max_length=255)

    # ===============================
    # SaaS upload (Source of Truth)
    # ===============================
    file_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="S3 object key (presigned upload)",
    )

    duration = models.PositiveIntegerField(null=True, blank=True)
    order = models.PositiveIntegerField(default=1)

    # 썸네일은 Worker가 생성
    thumbnail = models.ImageField(
        upload_to="thumbnails/",
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # --------------------------------------------------
    # 기본 재생 정책 (비디오 단위 default)
    # --------------------------------------------------
    allow_skip = models.BooleanField(default=False)
    max_speed = models.FloatField(default=1.0)
    show_watermark = models.BooleanField(default=True)

    # --------------------------------------------------
    # 정책 변경 즉시 반영을 위한 버전 (token versioning)
    # - 기존 API 계약 깨지지 않게 default=1
    # - 정책/권한 변경 시 증가시키면, 기존 토큰 즉시 무효화 가능
    # --------------------------------------------------
    policy_version = models.PositiveIntegerField(
        default=1,
        db_index=True,
        help_text="Increment on policy/permission changes to invalidate existing tokens",
    )

    # --------------------------------------------------
    # Worker 실패 사유 기록
    # --------------------------------------------------
    error_reason = models.TextField(blank=True, null=True, default="")

    # ===============================
    # HLS Output (Worker 결과)
    # ===============================
    hls_path = models.CharField(
        max_length=500,
        blank=True,
        help_text="HLS master playlist path (relative to CDN root)",
    )

    # --------------------------------------------------
    # Worker Lease (다중 노드 중복 처리 방지용)
    # - 기존 reclaim(updated_at 기반) 구조를 유지하면서,
    #   중앙 통제용 lease 정보를 "추가"로 기록한다.
    # - 서버/워커가 서로 내부 구현 몰라도 동작(헤더만 사용).
    # --------------------------------------------------
    processing_started_at = models.DateTimeField(null=True, blank=True)
    leased_until = models.DateTimeField(null=True, blank=True)
    leased_by = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["order", "id"]
        indexes = [
            models.Index(fields=["status", "updated_at"]),
            models.Index(fields=["leased_until", "status"]),
        ]

    def __str__(self):
        return f"[{self.status}] {self.title}"

    @property
    def source_type(self) -> str:
        if self.file_key:
            return "s3"
        return "unknown"


# ========================================================
# Video Access (수강생별 override + 접근 규칙)
# - Replaces VideoPermission semantics (SSOT)
# - DB table kept as video_videopermission for migration safety
# ========================================================

class VideoAccess(models.Model):
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="permissions",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="video_permissions",
    )

    # Legacy field (deprecated, use access_mode instead)
    rule = models.CharField(
        max_length=20,
        choices=[
            ("free", "무제한"),
            ("once", "1회 제한"),
            ("blocked", "제한"),
        ],
        default="free",
        null=True,
        blank=True,
        help_text="DEPRECATED: Use access_mode instead",
    )

    access_mode = models.CharField(
        max_length=20,
        choices=AccessMode.choices,
        default=AccessMode.FREE_REVIEW,
        db_index=True,
        help_text="Access mode: FREE_REVIEW, PROCTORED_CLASS, or BLOCKED",
    )

    allow_skip_override = models.BooleanField(null=True, blank=True)
    max_speed_override = models.FloatField(null=True, blank=True)
    show_watermark_override = models.BooleanField(null=True, blank=True)

    block_speed_control = models.BooleanField(default=False)
    block_seek = models.BooleanField(default=False)

    is_override = models.BooleanField(default=False)

    # Set when PROCTORED_CLASS watch is completed -> auto-upgrade to FREE_REVIEW
    proctored_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the monitored class-substitute watch was completed",
    )

    class Meta:
        db_table = "video_videopermission"  # Keep existing table name
        constraints = [
            models.UniqueConstraint(
                fields=["video", "enrollment"],
                name="unique_video_permission",
            )
        ]

    def __str__(self):
        return f"{self.enrollment.student.name} {self.video.title} ({self.access_mode})"


# Backward compatibility alias
VideoPermission = VideoAccess


# ========================================================
# Video Progress
# ========================================================

class VideoProgress(models.Model):
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="progresses",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="video_progress",
    )

    progress = models.FloatField(default=0)
    last_position = models.IntegerField(default=0)
    completed = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["video", "enrollment"],
                name="unique_video_progress",
            )
        ]

    def __str__(self):
        return (
            f"{self.enrollment.student.name} - "
            f"{self.video.title} ({self.progress * 100:.1f}%)"
        )


# ========================================================
# Video Playback Session (세션 / 감사)
# ========================================================

class VideoPlaybackSession(TimestampModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "활성"
        ENDED = "ENDED", "종료"
        REVOKED = "REVOKED", "차단"
        EXPIRED = "EXPIRED", "만료"

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="playback_sessions",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="playback_sessions",
    )

    session_id = models.CharField(max_length=64, db_index=True)
    device_id = models.CharField(max_length=128, db_index=True)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    
    # DB-based session management fields (Redis removal)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="Session expiration time")
    last_seen = models.DateTimeField(null=True, blank=True, help_text="Last heartbeat time")
    violated_count = models.IntegerField(default=0, help_text="Number of violations")
    total_count = models.IntegerField(default=0, help_text="Total event count")
    is_revoked = models.BooleanField(default=False, db_index=True, help_text="Whether session is revoked")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session_id"],
                name="uniq_video_playback_session_id",
            )
        ]
        indexes = [
            models.Index(fields=["status", "started_at"]),
            models.Index(fields=["video", "enrollment", "status"]),
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["enrollment", "status"]),
        ]

    def __str__(self):
        return f"{self.video_id}/{self.enrollment_id} {self.session_id} {self.status}"


# ========================================================
# Video Playback Event (v1: Audit only)
# ========================================================

class VideoPlaybackEvent(TimestampModel):
    class EventType(models.TextChoices):
        VISIBILITY_HIDDEN = "VISIBILITY_HIDDEN", "탭 숨김"
        VISIBILITY_VISIBLE = "VISIBILITY_VISIBLE", "탭 노출"
        FOCUS_LOST = "FOCUS_LOST", "포커스 이탈"
        FOCUS_GAINED = "FOCUS_GAINED", "포커스 복귀"
        SEEK_ATTEMPT = "SEEK_ATTEMPT", "탐색 시도"
        SPEED_CHANGE_ATTEMPT = "SPEED_CHANGE_ATTEMPT", "배속 변경 시도"
        FULLSCREEN_ENTER = "FULLSCREEN_ENTER", "전체화면 진입"
        FULLSCREEN_EXIT = "FULLSCREEN_EXIT", "전체화면 종료"
        PLAYER_ERROR = "PLAYER_ERROR", "플레이어 오류"

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="playback_events",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="video_playback_events",
    )

    session_id = models.CharField(max_length=64, db_index=True)
    user_id = models.BigIntegerField(db_index=True)

    event_type = models.CharField(
        max_length=32,
        choices=EventType.choices,
        db_index=True,
    )

    event_payload = models.JSONField(default=dict, blank=True)
    policy_snapshot = models.JSONField(default=dict, blank=True)

    violated = models.BooleanField(default=False, db_index=True)
    violation_reason = models.CharField(max_length=64, blank=True, default="")

    occurred_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["video", "enrollment", "session_id"], name="video_playback_event_session_idx"),
            models.Index(fields=["user_id", "session_id"], name="video_playback_event_user_idx"),
            # 부분 인덱스: 위반 이벤트만 인덱싱 (INSERT 성능 향상, 인덱스 크기 50% 감소)
            models.Index(
                fields=["event_type", "received_at"],
                condition=models.Q(violated=True),
                name="video_playback_event_violated_idx"
            ),
        ]
        ordering = ["-received_at", "-id"]

    def __str__(self):
        return f"{self.session_id} {self.event_type} v={self.violated}"
