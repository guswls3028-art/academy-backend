# PATH: apps/support/video/views/permission_views.py

from django.db import models, transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import VideoAccess, Video, AccessMode
from ..serializers import VideoAccessSerializer


class VideoPermissionViewSet(ModelViewSet):
    """Video access overrides (API: video-permissions for backward compat)."""
    queryset = VideoAccess.objects.all()
    serializer_class = VideoAccessSerializer

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_set(self, request):
        video_id = request.data.get("video_id")
        enrollments = request.data.get("enrollments", [])
        
        # Support both legacy 'rule' and new 'access_mode'
        rule = request.data.get("rule")  # Legacy
        access_mode_str = request.data.get("access_mode")  # New
        
        # Map legacy rule to access_mode if needed
        if rule and not access_mode_str:
            rule_to_mode = {
                "free": AccessMode.FREE_REVIEW,
                "once": AccessMode.PROCTORED_CLASS,
                "blocked": AccessMode.BLOCKED,
            }
            access_mode = rule_to_mode.get(rule, AccessMode.FREE_REVIEW)
        elif access_mode_str:
            access_mode = AccessMode(access_mode_str)
        else:
            access_mode = AccessMode.PROCTORED_CLASS  # Default

        objs = []
        for enrollment_id in enrollments:
            obj, _ = VideoAccess.objects.update_or_create(
                video_id=video_id,
                enrollment_id=enrollment_id,
                defaults={
                    "access_mode": access_mode,
                    "rule": rule or "free",  # Legacy field
                    "is_override": True,
                },
            )
            objs.append(obj)

        # ✅ 정책 변경 → policy_version 증가 (기존 토큰 즉시 무효화)
        Video.objects.filter(id=video_id).update(
            policy_version=models.F("policy_version") + 1
        )

        return Response(VideoAccessSerializer(objs, many=True).data)
