# apps/support/media/serializers.py

from django.conf import settings
from rest_framework import serializers

from apps.domains.lectures.models import Session
from .models import (
    Video,
    VideoPermission,
    VideoProgress,
    VideoPlaybackEvent,
)

# ========================================================
# Video
# ========================================================

class VideoSerializer(serializers.ModelSerializer):
    # 생성 시 session 지정 (write only)
    session = serializers.PrimaryKeyRelatedField(
        queryset=Session.objects.all(),
        write_only=True,
    )

    # 응답용 session_id
    session_id = serializers.IntegerField(
        source="session.id",
        read_only=True,
    )

    source_type = serializers.SerializerMethodField()

    # ✅ CDN 기반 파생 필드 (READ ONLY)
    thumbnail_url = serializers.SerializerMethodField()
    hls_url = serializers.SerializerMethodField()

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
            "allow_skip",
            "max_speed",
            "show_watermark",
            "thumbnail",      # 🔒 내부 저장용 (상대경로)
            "thumbnail_url",  # ✅ 외부 CDN URL
            "hls_path",       # 🔒 내부 저장용 (상대경로)
            "hls_url",        # ✅ 외부 CDN URL
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
        ref_name = "MediaVideo"

    # ----------------------------
    # Derived helpers
    # ----------------------------

    def get_source_type(self, obj):
        return "s3" if obj.file_key else "unknown"

    def _normalize_media_path(self, path: str) -> str:
        """
        DB에 저장된 상대경로를 CDN 기준 경로로 정규화
        - storage/media/... → media/...
        - media/... → media/...
        """
        if path.startswith("storage/media/"):
            return path[len("storage/media/"):]
        return path.lstrip("/")

    def _cdn_url(self, rel_path: str) -> str:
        base = settings.CDN_HLS_BASE_URL.rstrip("/")
        return f"{base}/{rel_path}"

    # ----------------------------
    # CDN fields
    # ----------------------------

    def get_thumbnail_url(self, obj):
        if not obj.thumbnail:
            return None
        rel = self._normalize_media_path(obj.thumbnail)
        return self._cdn_url(rel)

    def get_hls_url(self, obj):
        if not obj.hls_path:
            return None
        rel = self._normalize_media_path(obj.hls_path)
        return self._cdn_url(rel)


class VideoDetailSerializer(VideoSerializer):
    class Meta(VideoSerializer.Meta):
        ref_name = "MediaVideoDetail"


# ========================================================
# Permission / Progress
# ========================================================

class VideoPermissionSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name",
        read_only=True,
    )

    class Meta:
        model = VideoPermission
        fields = "__all__"
        ref_name = "MediaVideoPermission"


class VideoProgressSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name",
        read_only=True,
    )

    class Meta:
        model = VideoProgress
        fields = "__all__"
        ref_name = "MediaVideoProgress"


# ========================================================
# Playback API (token-based)
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
    session_id = serializers.CharField()
    expires_at = serializers.IntegerField()
    policy = serializers.JSONField()
    play_url = serializers.CharField()


# ========================================================
# Facade Playback Session
# ========================================================

class PlaybackSessionSerializer(serializers.Serializer):
    video_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()
    session_id = serializers.CharField()
    expires_at = serializers.IntegerField()


class PlaybackStartFacadeRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=128)


# ========================================================
# Playback Events
# ========================================================

class PlaybackEventItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=VideoPlaybackEvent.EventType.choices)
    occurred_at = serializers.IntegerField(required=False)
    payload = serializers.JSONField(required=False)


class PlaybackEventBatchRequestSerializer(serializers.Serializer):
    token = serializers.CharField()
    events = PlaybackEventItemSerializer(many=True)


class PlaybackEventBatchResponseSerializer(serializers.Serializer):
    stored = serializers.IntegerField()


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
        ref_name = "MediaVideoPlaybackEventList"

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
            "VISIBILITY_VISIBLE": 0,
            "FOCUS_LOST": 2,
            "FOCUS_GAINED": 0,
            "SEEK_ATTEMPT": 3,
            "SPEED_CHANGE_ATTEMPT": 3,
            "FULLSCREEN_ENTER": 0,
            "FULLSCREEN_EXIT": 0,
            "PLAYER_ERROR": 1,
        }
        score = int(weights.get(obj.event_type, 1))
        if obj.violated:
            score *= 2
        if obj.violation_reason:
            score += 1
        return score


class VideoRiskRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()
    score = serializers.IntegerField()
    danger = serializers.IntegerField()
    warn = serializers.IntegerField()
    info = serializers.IntegerField()
    last_occurred_at = serializers.DateTimeField(allow_null=True)
