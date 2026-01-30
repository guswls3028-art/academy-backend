# PATH: apps/support/video/views/permission_views.py

from django.db import models, transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import VideoPermission, Video
from ..serializers import VideoPermissionSerializer


class VideoPermissionViewSet(ModelViewSet):
    queryset = VideoPermission.objects.all()
    serializer_class = VideoPermissionSerializer

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_set(self, request):
        video_id = request.data.get("video_id")
        enrollments = request.data.get("enrollments", [])
        rule = request.data.get("rule", "once")

        objs = []
        for enrollment_id in enrollments:
            obj, _ = VideoPermission.objects.update_or_create(
                video_id=video_id,
                enrollment_id=enrollment_id,
                defaults={
                    "rule": rule,
                    "is_override": True,
                },
            )
            objs.append(obj)

        # ✅ 정책 변경 → policy_version 증가 (기존 토큰 즉시 무효화)
        Video.objects.filter(id=video_id).update(
            policy_version=models.F("policy_version") + 1
        )

        return Response(VideoPermissionSerializer(objs, many=True).data)
