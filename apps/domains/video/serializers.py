
from django.conf import settings
from rest_framework import serializers

from .models import (
    Video,
    VideoAccess,
    VideoProgress,
    VideoPlaybackEvent,
    VideoFolder,
)
from .encoding_progress import (
    get_video_encoding_progress,
    get_video_encoding_remaining_seconds,
    get_video_encoding_step_detail,
)
from academy.adapters.db.django import repositories_video as video_repo

# ========================================================
# Video
# ========================================================

class VideoSerializer(serializers.ModelSerializer):
    """
    ✅ FINAL SEALED VERSION (SaaS production)

    - DB 저장 필드와 API 노출 필드 분리
    - CDN 기반 URL 동적 생성
    - Cache-busting 지원
    - Legacy 경로 normalize
    """

    # write — queryset is overridden in __init__ to enforce tenant isolation
    session = serializers.PrimaryKeyRelatedField(
        queryset=video_repo.session_all_queryset(),
        write_only=True,
    )

    # read
    session_id = serializers.IntegerField(
        source="session.id",
        read_only=True,
    )

    source_type = serializers.SerializerMethodField()

    # CDN derived
    thumbnail_url = serializers.SerializerMethodField()
    hls_url = serializers.SerializerMethodField()

    # Redis 기반 인코딩 진행률 (status=PROCESSING 일 때만 유의미)
    encoding_progress = serializers.SerializerMethodField()
    encoding_remaining_seconds = serializers.SerializerMethodField()
    # 구간별 진행률 (n/7) 단계명 + 구간 내 0~100%
    encoding_step_index = serializers.SerializerMethodField()
    encoding_step_total = serializers.SerializerMethodField()
    encoding_step_name = serializers.SerializerMethodField()
    encoding_step_percent = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            "id",
            "session",
            "session_id",
            "title",
            "file_key",
            "duration",
            "order",
            "status",
            "encoding_progress",
            "encoding_remaining_seconds",
            "encoding_step_index",
            "encoding_step_total",
            "encoding_step_name",
            "encoding_step_percent",
            "allow_skip",
            "max_speed",
            "show_watermark",
            "thumbnail",
            "thumbnail_r2_key",
            "thumbnail_url",
            "hls_path",
            "hls_url",
            "visibility",
            "created_at",
            "updated_at",
            "source_type",
        ]
        read_only_fields = [
            "id",
            "session_id",
            "created_at",
            "updated_at",
            "thumbnail",
            "thumbnail_r2_key",
            "hls_path",
            "thumbnail_url",
            "hls_url",
            "encoding_progress",
            "encoding_remaining_seconds",
            "encoding_step_index",
            "encoding_step_total",
            "encoding_step_name",
            "encoding_step_percent",
        ]
        ref_name = "SealedVideo"

    def validate_session(self, value):
        """🔐 크로스 테넌트 세션 연결 방지: 세션이 요청 테넌트 소속인지 확인."""
        request = self.context.get("request")
        if request:
            tenant = getattr(request, "tenant", None)
            if tenant and hasattr(value, "lecture") and value.lecture:
                if getattr(value.lecture, "tenant_id", None) != tenant.id:
                    raise serializers.ValidationError(
                        "Session does not belong to your program."
                    )
        return value

    # ---------------------------
    # helpers
    # ---------------------------

    def get_encoding_progress(self, obj):
        """PROCESSING 상태일 때만 Redis에서 진행률 조회 (0..100 또는 null)."""
        if obj.status != Video.Status.PROCESSING:
            return None
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        tenant_id = None
        try:
            tenant_id = obj.session.lecture.tenant_id if hasattr(obj, 'session') and obj.session and hasattr(obj.session, 'lecture') and obj.session.lecture else None
        except Exception:
            pass
        pct = get_video_encoding_progress(int(obj.id), tenant_id=tenant_id)
        return pct if pct is not None else None

    def get_encoding_remaining_seconds(self, obj):
        """PROCESSING 상태일 때만 Redis에서 예상 남은 시간(초) 조회."""
        if obj.status != Video.Status.PROCESSING:
            return None
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        tenant_id = None
        try:
            tenant_id = obj.session.lecture.tenant_id if hasattr(obj, 'session') and obj.session and hasattr(obj.session, 'lecture') and obj.session.lecture else None
        except Exception:
            pass
        return get_video_encoding_remaining_seconds(int(obj.id), tenant_id=tenant_id)

    def get_encoding_step_index(self, obj):
        if obj.status != Video.Status.PROCESSING:
            return None
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        tenant_id = None
        try:
            tenant_id = obj.session.lecture.tenant_id if hasattr(obj, 'session') and obj.session and hasattr(obj.session, 'lecture') and obj.session.lecture else None
        except Exception:
            pass
        d = get_video_encoding_step_detail(int(obj.id), tenant_id=tenant_id)
        return d.get("step_index") if d else None

    def get_encoding_step_total(self, obj):
        if obj.status != Video.Status.PROCESSING:
            return None
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        tenant_id = None
        try:
            tenant_id = obj.session.lecture.tenant_id if hasattr(obj, 'session') and obj.session and hasattr(obj.session, 'lecture') and obj.session.lecture else None
        except Exception:
            pass
        d = get_video_encoding_step_detail(int(obj.id), tenant_id=tenant_id)
        return d.get("step_total") if d else None

    def get_encoding_step_name(self, obj):
        if obj.status != Video.Status.PROCESSING:
            return None
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        tenant_id = None
        try:
            tenant_id = obj.session.lecture.tenant_id if hasattr(obj, 'session') and obj.session and hasattr(obj.session, 'lecture') and obj.session.lecture else None
        except Exception:
            pass
        d = get_video_encoding_step_detail(int(obj.id), tenant_id=tenant_id)
        return d.get("step_name_display") if d else None

    def get_encoding_step_percent(self, obj):
        if obj.status != Video.Status.PROCESSING:
            return None
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        tenant_id = None
        try:
            tenant_id = obj.session.lecture.tenant_id if hasattr(obj, 'session') and obj.session and hasattr(obj.session, 'lecture') and obj.session.lecture else None
        except Exception:
            pass
        d = get_video_encoding_step_detail(int(obj.id), tenant_id=tenant_id)
        return d.get("step_percent") if d else None

    def get_source_type(self, obj):
        return "s3" if obj.file_key else "unknown"

    def _cdn_base(self) -> str | None:
        base = getattr(settings, "CDN_HLS_BASE_URL", None)
        return base.rstrip("/") if base else None

    def _normalize_media_path(self, path: str) -> str:
        path = path.lstrip("/")

        if path.startswith("media/"):
            return path

        if path.startswith("storage/media/"):
            return path[len("storage/"):]

        return path

    def _cache_version(self, obj) -> int:
        try:
            return int(obj.updated_at.timestamp())
        except Exception:
            return 0

    def _build_cdn_url(self, cdn: str, rel_path: str, *, version: int) -> str:
        """
        cdn base + path 를 합치고, 서명 시크릿이 있으면 HMAC 쿼리를 부착한다.
        CDN Worker 가 sig/exp 를 검증하므로, signing_secret 가 비어 있으면
        unsigned URL 을 그대로 돌려 보내 R2 public 단계와 호환 유지.
        """
        path = "/" + rel_path.lstrip("/")
        secret = getattr(settings, "CDN_HLS_SIGNING_SECRET", "") or ""
        if not secret:
            return f"{cdn}{path}?v={version}"
        from .cdn.cloudflare_signing import CloudflareSignedURL
        from django.utils import timezone
        ttl = int(getattr(settings, "CDN_HLS_LIST_URL_TTL_SECONDS", 6 * 3600))
        expires_at = int(timezone.now().timestamp()) + ttl
        signer = CloudflareSignedURL(
            secret=str(secret),
            key_id=str(getattr(settings, "CDN_HLS_SIGNING_KEY_ID", "v1")),
        )
        return signer.build_url(
            cdn_base=cdn,
            path=path,
            expires_at=expires_at,
            user_id=None,
            extra_query={"v": str(version)},
        )

    # ---------------------------
    # CDN fields
    # ---------------------------

    def get_thumbnail_url(self, obj):
        cdn = self._cdn_base()
        if not cdn:
            return None

        version = self._cache_version(obj)

        # 1️⃣ thumbnail_r2_key (SSOT — Worker가 항상 채움)
        r2_key = (getattr(obj, "thumbnail_r2_key", "") or "").strip()
        if r2_key:
            return self._build_cdn_url(cdn, self._normalize_media_path(r2_key), version=version)

        # 2️⃣ legacy ImageField (deprecated, 점진 제거)
        if obj.thumbnail:
            return self._build_cdn_url(cdn, self._normalize_media_path(obj.thumbnail.name), version=version)

        # 3️⃣ READY fallback — video.tenant_id 직접 (V1.1 SSOT). session 체인 폐기.
        if obj.status == obj.Status.READY:
            tenant_id = getattr(obj, "tenant_id", None)
            if tenant_id is None:
                return None
            from apps.core.r2_paths import video_hls_prefix
            rel = self._normalize_media_path(
                f"{video_hls_prefix(tenant_id=tenant_id, video_id=obj.id)}/thumbnail.jpg"
            )
            return self._build_cdn_url(cdn, rel, version=version)

        return None

    def get_hls_url(self, obj):
        if not obj.hls_path:
            return None

        cdn = self._cdn_base()
        if not cdn:
            return None

        return self._build_cdn_url(
            cdn,
            self._normalize_media_path(str(obj.hls_path)),
            version=self._cache_version(obj),
        )


class VideoDetailSerializer(VideoSerializer):
    can_retry = serializers.SerializerMethodField()

    class Meta(VideoSerializer.Meta):
        fields = VideoSerializer.Meta.fields + ["can_retry"]
        ref_name = "SealedVideoDetail"

    def get_can_retry(self, obj):
        """
        Retry 버튼 표시 여부를 서버에서 판단.
        - PENDING + file_key → True (upload-complete 재실행)
        - FAILED, UPLOADED → True
        - PROCESSING/READY + current_job RUNNING(not cancel_requested) → False
        - PROCESSING/READY + no active job or stale job → True
        """
        RETRY_ALLOWED = {
            Video.Status.PENDING,
            Video.Status.FAILED,
            Video.Status.UPLOADED,
            Video.Status.PROCESSING,
            Video.Status.READY,
        }

        st = obj.status
        if st not in RETRY_ALLOWED:
            return False

        if st == Video.Status.PENDING:
            return bool((obj.file_key or "").strip())

        # Check current job state
        job_id = getattr(obj, "current_job_id", None)
        if job_id:
            try:
                from datetime import timedelta
                from django.conf import settings
                from django.utils import timezone
                from apps.domains.video.models import VideoTranscodeJob
                cur = VideoTranscodeJob.objects.filter(pk=job_id).only(
                    "state", "cancel_requested", "last_heartbeat_at", "updated_at",
                ).first()
                if cur and cur.state == VideoTranscodeJob.State.RUNNING and not getattr(cur, "cancel_requested", False):
                    # Allow retry if RUNNING job is stale (no heartbeat for too long)
                    stale_minutes = getattr(settings, "VIDEO_RETRY_STALE_RUNNING_MINUTES", 30)
                    last_activity = cur.last_heartbeat_at or cur.updated_at
                    if last_activity >= timezone.now() - timedelta(minutes=stale_minutes):
                        return False
                    # Stale RUNNING → allow retry
                if cur and cur.state in (VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RETRY_WAIT):
                    return False
            except Exception:
                pass

        return True


# ========================================================
# Permission / Progress
# ========================================================

class VideoAccessSerializer(serializers.ModelSerializer):
    """API uses access_mode (SSOT). DB table kept as video_videopermission."""
    student_name = serializers.CharField(
        source="enrollment.student.name",
        read_only=True,
    )

    class Meta:
        model = VideoAccess
        fields = "__all__"
        ref_name = "SealedVideoAccess"


# Backward compat alias
VideoPermissionSerializer = VideoAccessSerializer


class VideoProgressSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name",
        read_only=True,
    )

    # ✅ 진행률 % (프론트 표시용)
    progress_percent = serializers.SerializerMethodField()

    class Meta:
        model = VideoProgress
        fields = "__all__"
        ref_name = "SealedVideoProgress"

    def get_progress_percent(self, obj):
        try:
            return round(float(obj.progress or 0) * 100, 1)
        except Exception:
            return 0.0


# ========================================================
# Playback API
# ========================================================

class PlaybackStartRequestSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    device_id = serializers.CharField(max_length=128)


class PlaybackRefreshRequestSerializer(serializers.Serializer):
    token = serializers.CharField()


class PlaybackHeartbeatRequestSerializer(serializers.Serializer):
    token = serializers.CharField()


class PlaybackEndRequestSerializer(serializers.Serializer):
    token = serializers.CharField()


class PlaybackResponseSerializer(serializers.Serializer):
    token = serializers.CharField()
    session_id = serializers.CharField(allow_null=True, required=False)  # None for FREE_REVIEW
    expires_at = serializers.IntegerField(allow_null=True, required=False)  # None for FREE_REVIEW
    access_mode = serializers.ChoiceField(
        choices=["FREE_REVIEW", "PROCTORED_CLASS", "BLOCKED"],
        required=True,
    )
    monitoring_enabled = serializers.BooleanField()
    policy = serializers.JSONField()
    play_url = serializers.CharField()


# ========================================================
# Events
# ========================================================

class PlaybackEventItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(
        choices=VideoPlaybackEvent.EventType.choices
    )
    occurred_at = serializers.IntegerField(required=False)
    payload = serializers.JSONField(required=False)


class PlaybackEventBatchRequestSerializer(serializers.Serializer):
    token = serializers.CharField()
    events = PlaybackEventItemSerializer(many=True)


class PlaybackEventBatchResponseSerializer(serializers.Serializer):
    stored = serializers.IntegerField()


# ========================================================
# Event List (Admin Analytics)
# ========================================================

class VideoPlaybackEventListSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name",
        read_only=True,
    )
    enrollment_id = serializers.IntegerField(
        source="enrollment.id",
        read_only=True,
    )

    severity = serializers.SerializerMethodField()
    score = serializers.SerializerMethodField()

    class Meta:
        model = VideoPlaybackEvent
        fields = [
            "id",
            "video",
            "enrollment_id",
            "student_name",
            "session_id",
            "user_id",
            "event_type",
            "violated",
            "violation_reason",
            "event_payload",
            "policy_snapshot",
            "occurred_at",
            "received_at",
            "severity",
            "score",
        ]
        ref_name = "SealedVideoPlaybackEventList"

    # ---------------------------
    # Risk classification
    # ---------------------------

    def get_severity(self, obj):
        base = {
            "SEEK_ATTEMPT": "warn",
            "SPEED_CHANGE_ATTEMPT": "warn",
            "FOCUS_LOST": "warn",
            "VISIBILITY_HIDDEN": "info",
            "PLAYER_ERROR": "info",
        }.get(obj.event_type, "info")

        return "danger" if obj.violated else base

    def get_score(self, obj):
        weights = {
            "VISIBILITY_HIDDEN": 1,
            "FOCUS_LOST": 2,
            "SEEK_ATTEMPT": 3,
            "SPEED_CHANGE_ATTEMPT": 3,
            "PLAYER_ERROR": 1,
        }

        w = int(weights.get(obj.event_type, 1))

        if obj.violated:
            w *= 2
        if obj.violation_reason:
            w += 1

        return w


# ========================================================
# Aggregated Risk Row
# ========================================================

class VideoRiskRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()
    score = serializers.IntegerField()
    danger = serializers.IntegerField()
    warn = serializers.IntegerField()
    info = serializers.IntegerField()
    last_occurred_at = serializers.DateTimeField(allow_null=True)


# ========================================================
# Video Folder
# ========================================================

class VideoFolderSerializer(serializers.ModelSerializer):
    """공개 영상 폴더 Serializer."""

    parent_id = serializers.IntegerField(source="parent.id", read_only=True, allow_null=True)
    session_id = serializers.IntegerField(source="session.id", read_only=True, allow_null=True)

    class Meta:
        model = VideoFolder
        fields = [
            "id",
            "name",
            "session_id",
            "parent_id",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
