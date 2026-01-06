# PATH: apps/domains/lectures/views.py

from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response

from .models import Lecture, Session
from .serializers import LectureSerializer, SessionSerializer


# ========================================================
# Lecture
# ========================================================

class LectureViewSet(ModelViewSet):
    queryset = Lecture.objects.all()
    serializer_class = LectureSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["is_active", "subject"]
    search_fields = ["title", "name", "subject"]


# ========================================================
# Session
# ========================================================

class SessionViewSet(ModelViewSet):
    serializer_class = SessionSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["lecture", "date"]
    search_fields = ["title"]

    def get_queryset(self):
        qs = Session.objects.all()

        lecture = self.request.query_params.get("lecture")
        if lecture:
            qs = qs.filter(lecture_id=lecture)

        date = self.request.query_params.get("date")
        if date:
            qs = qs.filter(date=date)

        # ✅ 선택: exam_id로도 필터 가능
        exam = self.request.query_params.get("exam")
        if exam:
            qs = qs.filter(exam_id=exam)

        return qs.order_by("order", "id")

    def create(self, request, *args, **kwargs):
        lecture_id = request.data.get("lecture")
        title = request.data.get("title")
        date = request.data.get("date")

        # ✅ NEW: exam은 optional
        exam_id = request.data.get("exam")

        if not lecture_id:
            return Response({"detail": "lecture 필드는 필수입니다"}, status=400)
        if not title:
            return Response({"detail": "title 필드는 필수입니다"}, status=400)

        last_session = (
            Session.objects.filter(lecture_id=lecture_id)
            .order_by("-order")
            .first()
        )
        next_order = last_session.order + 1 if last_session else 1

        data = {
            "lecture": lecture_id,
            "title": title,
            "date": date,
            "order": next_order,
            # ✅ 시험 연결 (없으면 None)
            "exam": exam_id if exam_id else None,
        }

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        return Response(serializer.data, status=201)
