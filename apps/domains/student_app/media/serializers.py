from rest_framework import serializers


class StudentVideoListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    session_id = serializers.IntegerField()
    title = serializers.CharField()

    status = serializers.CharField()
    thumbnail_url = serializers.CharField(allow_null=True, required=False)
    duration = serializers.IntegerField(allow_null=True, required=False)

    # 진행률 (0-100)
    progress = serializers.FloatField(required=False, default=0)
    completed = serializers.BooleanField(required=False, default=False)

    # 정책(단일 진실)
    allow_skip = serializers.BooleanField()
    max_speed = serializers.FloatField()
    show_watermark = serializers.BooleanField()

    # 학생별 적용 룰 (Legacy) — enrollment 없을 때 None
    effective_rule = serializers.ChoiceField(
        choices=["free", "once", "blocked"],
        required=False,
        allow_null=True,
    )
    # 접근 모드 — enrollment 없을 때 None (전체공개 등)
    access_mode = serializers.ChoiceField(
        choices=["FREE_REVIEW", "PROCTORED_CLASS", "BLOCKED"],
        required=False,
        allow_null=True,
    )


class StudentVideoPlaybackSerializer(serializers.Serializer):
    """
    학생 플레이어가 신뢰하는 단일 진실 payload
    """
    video = StudentVideoListItemSerializer()
    hls_url = serializers.CharField(allow_null=True, required=False)
    mp4_url = serializers.CharField(allow_null=True, required=False)
    play_url = serializers.CharField(allow_null=True, required=False)  # ✅ 재생 URL 추가

    policy = serializers.DictField()
