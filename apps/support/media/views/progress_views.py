# apps/support/media/views/progress_views.py

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from ..models import VideoProgress
from ..serializers import VideoProgressSerializer


class VideoProgressViewSet(ModelViewSet):
    queryset = VideoProgress.objects.all()
    serializer_class = VideoProgressSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated]
