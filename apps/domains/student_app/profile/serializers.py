# apps/domains/student_app/profile/serializers.py
from rest_framework import serializers


class StudentProfileSerializer(serializers.Serializer):
    """학생 본인 프로필 (GET 응답)"""
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    profile_photo_url = serializers.URLField(allow_null=True, read_only=True)


class StudentProfilePhotoUpdateSerializer(serializers.Serializer):
    """프로필 사진 업로드 (PATCH)"""
    profile_photo = serializers.ImageField(required=True)
