# apps/domains/progress/views.py
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from apps.core.permissions import TenantResolvedAndMember
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
    serializer_class = ProgressPolicySerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ProgressPolicyFilter
    search_fields = ["lecture__title", "lecture__name"]
    ordering_fields = ["id", "created_at", "updated_at"]
    ordering = ["-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return ProgressPolicy.objects.none()
        return ProgressPolicy.objects.filter(lecture__tenant=tenant).select_related("lecture")


class SessionProgressViewSet(ModelViewSet):
    serializer_class = SessionProgressSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SessionProgressFilter
    search_fields = ["enrollment_id", "session__title", "session__lecture__title"]
    ordering_fields = ["id", "created_at", "updated_at", "calculated_at", "completed"]
    ordering = ["-updated_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return SessionProgress.objects.none()
        return SessionProgress.objects.filter(session__lecture__tenant=tenant).select_related("session", "session__lecture")


class LectureProgressViewSet(ModelViewSet):
    serializer_class = LectureProgressSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = LectureProgressFilter
    search_fields = ["enrollment_id", "lecture__title", "lecture__name"]
    ordering_fields = ["id", "created_at", "updated_at", "risk_level", "completed_sessions"]
    ordering = ["-updated_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return LectureProgress.objects.none()
        return LectureProgress.objects.filter(lecture__tenant=tenant).select_related("lecture", "last_session")


class ClinicLinkViewSet(ModelViewSet):
    serializer_class = ClinicLinkSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ClinicLinkFilter
    search_fields = ["enrollment_id", "session__title", "session__lecture__title", "memo"]
    ordering_fields = ["id", "created_at", "updated_at", "approved", "is_auto"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return ClinicLink.objects.none()
        return ClinicLink.objects.filter(session__lecture__tenant=tenant).select_related("session", "session__lecture")


class RiskLogViewSet(ModelViewSet):
    serializer_class = RiskLogSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = RiskLogFilter
    search_fields = ["enrollment_id", "reason"]
    ordering_fields = ["id", "created_at", "updated_at", "risk_level", "rule"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return RiskLog.objects.none()
        return RiskLog.objects.filter(session__lecture__tenant=tenant).select_related("session")
