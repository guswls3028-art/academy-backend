# views/video_progress_views.py

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from ..models import VideoProgress
from ..serializers import VideoProgressSerializer
