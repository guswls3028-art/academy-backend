# PATH: apps/support/video/serializers.py

from django.conf import settings
from rest_framework import serializers

from .models import (
    Video,
    VideoAccess,
    VideoProgress,
    VideoPlaybackEvent,
)
from .encoding_progress import get_video_encoding_progress
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

    # write
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
            "allow_skip",
            "max_speed",
            "show_watermark",
            "thumbnail",
            "thumbnail_url",
            "hls_path",
            "hls_url",
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
            "hls_path",
            "thumbnail_url",
            "hls_url",
        ]
        ref_name = "SealedVideo"

    # ---------------------------
    # helpers
    # ---------------------------

    def get_encoding_progress(self, obj):
        """PROCESSING 상태일 때만 Redis에서 진행률 조회 (0..100 또는 null)."""
        if obj.status != Video.Status.PROCESSING:
            return None
        pct = get_video_encoding_progress(int(obj.id))
        return pct if pct is not None else None

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

    # ---------------------------
    # CDN fields
    # ---------------------------

    def get_thumbnail_url(self, obj):
        cdn = self._cdn_base()
        if not cdn:
            return None

        # 1️⃣ explicit thumbnail
        if obj.thumbnail:
            path = self._normalize_media_path(obj.thumbnail.name)
            return f"{cdn}/{path}?v={self._cache_version(obj)}"

        # 2️⃣ READY fallback (경로 통일: tenants/{id}/video/hls/...) — session/lecture/tenant 없으면 None
        if obj.status == obj.Status.READY:
            try:
                session = getattr(obj, "session", None)
                lecture = getattr(session, "lecture", None) if session else None
                tenant = getattr(lecture, "tenant", None) if lecture else None
                if tenant is None:
                    return None
                tenant_id = getattr(tenant, "id", None) or getattr(tenant, "pk", None)
                from apps.core.r2_paths import video_hls_prefix
                path = self._normalize_media_path(
                    f"{video_hls_prefix(tenant_id=tenant_id, video_id=obj.id)}/thumbnail.jpg"
                )
            except Exception:
                try:
                    code = getattr(getattr(getattr(obj, "session", None), "lecture", None), "tenant", None)
                    code = getattr(code, "code", None) if code else None
                    if code is None:
                        return None
                    path = self._normalize_media_path(
                        f"media/hls/videos/{code}/videos/{obj.id}/thumbnail.jpg"
                    )
                except Exception:
                    return None
            return f"{cdn}/{path}?v={self._cache_version(obj)}"

        return None

    def get_hls_url(self, obj):
        if not obj.hls_path:
            return None

        cdn = self._cdn_base()
        if not cdn:
            return None

        path = self._normalize_media_path(str(obj.hls_path))
        return f"{cdn}/{path}?v={self._cache_version(obj)}"


class VideoDetailSerializer(VideoSerializer):
    class Meta(VideoSerializer.Meta):
        ref_name = "SealedVideoDetail"


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
