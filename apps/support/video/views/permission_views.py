# PATH: apps/support/video/views/permission_views.py

from django.db import models, transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import AccessMode
from ..serializers import VideoAccessSerializer
from academy.adapters.db.django import repositories_video as video_repo


class VideoPermissionViewSet(ModelViewSet):
    """Video access overrides (API: video-permissions for backward compat)."""
    queryset = video_repo.video_access_all()
    serializer_class = VideoAccessSerializer

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_set(self, request):
        video_id = request.data.get("video_id")
        enrollments = request.data.get("enrollments", [])

        rule = request.data.get("rule")
        access_mode_str = request.data.get("access_mode")

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
            access_mode = AccessMode.PROCTORED_CLASS

        objs = []
        for enrollment_id in enrollments:
            obj, _ = video_repo.video_access_update_or_create_by_ids(
                video_id,
                enrollment_id,
                defaults={
                    "access_mode": access_mode,
                    "rule": rule or "free",
                    "is_override": True,
                },
            )
            objs.append(obj)

        video_repo.video_update(video_id, policy_version=models.F("policy_version") + 1)
        return Response(VideoAccessSerializer(objs, many=True).data)
