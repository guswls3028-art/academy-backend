# apps/domains/progress/views.py
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from .models import ProgressPolicy, SessionProgress, LectureProgress, ClinicLink, RiskLog
from .serializers import (
    ProgressPolicySerializer,
    SessionProgressSerializer,
    LectureProgressSerializer,
    ClinicLinkSerializer,
    RiskLogSerializer,
)
from .filters import (
    ProgressPolicyFilter,
    SessionProgressFilter,
    LectureProgressFilter,
    ClinicLinkFilter,
    RiskLogFilter,
)


class ProgressPolicyViewSet(ModelViewSet):
    queryset = ProgressPolicy.objects.select_related("lecture").all()
    serializer_class = ProgressPolicySerializer
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ProgressPolicyFilter
    search_fields = ["lecture__title", "lecture__name"]
    ordering_fields = ["id", "created_at", "updated_at"]
    ordering = ["-id"]


class SessionProgressViewSet(ModelViewSet):
    queryset = SessionProgress.objects.select_related("session", "session__lecture").all()
    serializer_class = SessionProgressSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SessionProgressFilter
    search_fields = ["enrollment_id", "session__title", "session__lecture__title"]
    ordering_fields = ["id", "created_at", "updated_at", "calculated_at", "completed"]
    ordering = ["-updated_at", "-id"]


class LectureProgressViewSet(ModelViewSet):
    queryset = LectureProgress.objects.select_related("lecture", "last_session").all()
    serializer_class = LectureProgressSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = LectureProgressFilter
    search_fields = ["enrollment_id", "lecture__title", "lecture__name"]
    ordering_fields = ["id", "created_at", "updated_at", "risk_level", "completed_sessions"]
    ordering = ["-updated_at", "-id"]


class ClinicLinkViewSet(ModelViewSet):
    queryset = ClinicLink.objects.select_related("session", "session__lecture").all()
    serializer_class = ClinicLinkSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ClinicLinkFilter
    search_fields = ["enrollment_id", "session__title", "session__lecture__title", "memo"]
    ordering_fields = ["id", "created_at", "updated_at", "approved", "is_auto"]
    ordering = ["-created_at", "-id"]


class RiskLogViewSet(ModelViewSet):
    queryset = RiskLog.objects.select_related("session").all()
    serializer_class = RiskLogSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = RiskLogFilter
    search_fields = ["enrollment_id", "reason"]
    ordering_fields = ["id", "created_at", "updated_at", "risk_level", "rule"]
    ordering = ["-created_at", "-id"]
