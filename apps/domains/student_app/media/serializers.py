from rest_framework import serializers


class StudentVideoListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    session_id = serializers.IntegerField()
    title = serializers.CharField()

    status = serializers.CharField()
    thumbnail_url = serializers.CharField(allow_null=True, required=False)

    # 정책(단일 진실)
    allow_skip = serializers.BooleanField()
    max_speed = serializers.FloatField()
    show_watermark = serializers.BooleanField()

    # 학생별 적용 룰 (Legacy)
    effective_rule = serializers.ChoiceField(
        choices=["free", "once", "blocked"],
        required=False,
    )
    
    # 새로운 접근 모드
    access_mode = serializers.ChoiceField(
        choices=["FREE_REVIEW", "PROCTORED_CLASS", "BLOCKED"],
        required=False,
    )


class StudentVideoPlaybackSerializer(serializers.Serializer):
    """
    학생 플레이어가 신뢰하는 단일 진실 payload
    """
    video = StudentVideoListItemSerializer()
    hls_url = serializers.CharField(allow_null=True, required=False)
    mp4_url = serializers.CharField(allow_null=True, required=False)

    policy = serializers.DictField()
