from rest_framework import serializers
from .models import (
    BoardCategory,
    BoardPost,
    BoardAttachment,
    BoardReadStatus,
)


class BoardCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BoardCategory
        fields = "__all__"


class BoardAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoardAttachment
        fields = "__all__"


class BoardPostSerializer(serializers.ModelSerializer):
    attachments = BoardAttachmentSerializer(
        many=True, read_only=True
    )

    class Meta:
        model = BoardPost
        fields = "__all__"


class BoardReadStatusSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name", read_only=True
    )

    class Meta:
        model = BoardReadStatus
        fields = "__all__"
