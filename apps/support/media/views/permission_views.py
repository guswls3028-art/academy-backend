# apps/support/media/views/permission_views.py

# test change

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from ..models import VideoPermission
from ..serializers import VideoPermissionSerializer


class VideoPermissionViewSet(ModelViewSet):
    queryset = VideoPermission.objects.all()
    serializer_class = VideoPermissionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated]
