# apps/support/media/views/progress_views.py

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from ..models import VideoProgress, VideoPermission
from ..serializers import VideoProgressSerializer


class VideoProgressViewSet(ModelViewSet):
    queryset = VideoProgress.objects.all()
    serializer_class = VideoProgressSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated]

    def perform_update(self, serializer):
        vp = serializer.instance
        prev_completed = vp.completed

        vp = serializer.save()

        # 🔥 once → free 자동 승격 (completed False → True 순간)
        if not prev_completed and vp.completed:
            VideoPermission.objects.filter(
                video=vp.video,
                enrollment=vp.enrollment,
                rule="once",
            ).update(
                rule="free",
                is_override=False,
            )
