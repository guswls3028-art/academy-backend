from rest_framework import serializers

from .models import (
    Video,
    VideoPermission,
    VideoProgress,
    VideoPlaybackSession,
    VideoPlaybackEvent,
)

from rest_framework import serializers
from apps.support.media.models import Video
from apps.domains.lectures.models import Session


class VideoSerializer(serializers.ModelSerializer):
    # 🔥 핵심 1: 생성 시 받을 session (write only)
    session = serializers.PrimaryKeyRelatedField(
        queryset=Session.objects.all(),
        write_only=True,
    )

    # 🔥 핵심 2: 응답용 session_id (read only)
    session_id = serializers.IntegerField(
        source="session.id",
        read_only=True,
    )

    source_type = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            "id",
            "session",        # ✅ 추가
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


class VideoDetailSerializer(VideoSerializer):
    class Meta(VideoSerializer.Meta):
        ref_name = "MediaVideoDetail"



# ========================================================
# Playback API (v1)
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
# Playback Session (Student Facade API)
# ========================================================

class PlaybackSessionSerializer(serializers.Serializer):
    """
    Facade API 전용 Serializer

    ⚠️ 주의
    - VideoPlaybackSession(Model)과 무관
    - create_playback_session() 반환 dict 전용
    """

    video_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()
    session_id = serializers.CharField()
    expires_at = serializers.IntegerField()


# ========================================================
# Event collection (v1: audit-only)
# ========================================================

class PlaybackEventItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=VideoPlaybackEvent.EventType.choices)
    occurred_at = serializers.IntegerField(required=False)  # epoch seconds
    payload = serializers.JSONField(required=False)


class PlaybackEventBatchRequestSerializer(serializers.Serializer):
    token = serializers.CharField()
    events = PlaybackEventItemSerializer(many=True)


class PlaybackEventBatchResponseSerializer(serializers.Serializer):
    stored = serializers.IntegerField()
