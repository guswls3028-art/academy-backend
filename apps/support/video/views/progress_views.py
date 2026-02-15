# apps/support/video/views/progress_views.py

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from django.utils import timezone
from ..models import VideoProgress, VideoAccess, AccessMode
from ..serializers import VideoProgressSerializer
from academy.adapters.db.django import repositories_video as video_repo


class VideoProgressViewSet(ModelViewSet):
    queryset = video_repo.video_progress_all()
    serializer_class = VideoProgressSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated]

    def perform_update(self, serializer):
        vp = serializer.instance
        prev_completed = vp.completed

        vp = serializer.save()

        # PROCTORED_CLASS → FREE_REVIEW on completion (SSOT)
        if not prev_completed and vp.completed:
            now = timezone.now()
            video_repo.video_access_filter(vp.video, vp.enrollment).filter(
                access_mode=AccessMode.PROCTORED_CLASS,
            ).update(
                access_mode=AccessMode.FREE_REVIEW,
                proctored_completed_at=now,
                is_override=False,
            )
            video_repo.video_access_filter(vp.video, vp.enrollment).filter(
                rule="once",
            ).update(rule="free", is_override=False)
