from rest_framework import serializers

from .models import PushNotificationConfig
from .security import is_allowed_web_push_endpoint


class PushSubscribeSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=500)
    p256dh_key = serializers.CharField(max_length=200)
    auth_key = serializers.CharField(max_length=200)
    user_agent = serializers.CharField(max_length=300, required=False, default="")

    def validate_endpoint(self, value):
        if not is_allowed_web_push_endpoint(value):
            raise serializers.ValidationError("지원되는 Web Push 주소가 아닙니다.")
        return value


class PushUnsubscribeSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=500)

    def validate_endpoint(self, value):
        if not is_allowed_web_push_endpoint(value):
            raise serializers.ValidationError("지원되는 Web Push 주소가 아닙니다.")
        return value


class PushNotificationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushNotificationConfig
        fields = [
            "student_registration",
            "qna_new_question",
            "exam_submission",
            "clinic_booking",
            "video_encoding_complete",
        ]
