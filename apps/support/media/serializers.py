# apps/support/media/serializers.py

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
            "hls_path",
            "created_at",
            "updated_at",
            "source_type",
        ]
        read_only_fields = [
            "id",
            "session_id",
            "created_at",
            "updated_at",
        ]
        ref_name = "MediaVideo"

    def get_source_type(self, obj):
        return "s3" if obj.file_key else "unknown"


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
# Facade Playback Session (dict 기반)
# ========================================================

class PlaybackSessionSerializer(serializers.Serializer):
    """
    Facade API 전용 Serializer
    - DB 모델과 무관
    - create_playback_session() 반환 dict 전용
    """
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
        ]
        ref_name = "MediaVideoPlaybackEventList"
