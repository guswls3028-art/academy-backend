from rest_framework import serializers

from .models import PushSubscription, PushNotificationConfig


class PushSubscribeSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=500)
    p256dh_key = serializers.CharField(max_length=200)
    auth_key = serializers.CharField(max_length=200)
    user_agent = serializers.CharField(max_length=300, required=False, default="")


class PushUnsubscribeSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=500)


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
