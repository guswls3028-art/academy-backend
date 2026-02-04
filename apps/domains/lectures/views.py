# PATH: apps/domains/lectures/views.py

from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import PermissionDenied

from .models import Lecture, Session
from .serializers import LectureSerializer, SessionSerializer


class LectureViewSet(ModelViewSet):
    serializer_class = LectureSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["is_active", "subject"]
    search_fields = ["title", "name", "subject"]

    def get_queryset(self):
        """
        🔐 tenant 단일 진실
        """
        return Lecture.objects.filter(tenant=self.request.tenant)

    def perform_create(self, serializer):
        """
        🔐 Lecture 생성 시 tenant 강제 주입
        """
        serializer.save(tenant=self.request.tenant)


class SessionViewSet(ModelViewSet):
    serializer_class = SessionSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["lecture", "date"]
    search_fields = ["title"]

    def get_queryset(self):
        """
        Session은 lecture를 통해 tenant가 결정됨
        """
        qs = Session.objects.select_related("lecture")
        qs = qs.filter(lecture__tenant=self.request.tenant)

        lecture = self.request.query_params.get("lecture")
        if lecture:
            qs = qs.filter(lecture_id=lecture)

        date = self.request.query_params.get("date")
        if date:
            qs = qs.filter(date=date)

        return qs.order_by("order", "id")

    def perform_create(self, serializer):
        """
        🔐 Session 생성 시 lecture.tenant 검증
        """
        lecture = serializer.validated_data["lecture"]
        if lecture.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 강의에는 세션을 추가할 수 없습니다.")

        serializer.save()
