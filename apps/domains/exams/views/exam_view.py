# PATH: apps/domains/exams/views/exam_view.py

from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer
from apps.domains.lectures.models import Session


class ExamViewSet(ModelViewSet):
    """
    ✅ SaaS 표준 Exam 조회 / 생성 API

    지원:
    - GET /exams/?session_id=123
    - GET /exams/?lecture_id=10

    생성 규칙:
    - session_id는 프론트에서 전달
    - subject는 session → lecture → subject 기준으로 자동 결정
    """

    queryset = Exam.objects.all()
    serializer_class = ExamSerializer
    permission_classes = [IsAuthenticated]

    # ======================================================
    # CREATE
    # ======================================================
    def perform_create(self, serializer):
        """
        Exam 생성 시 처리 규칙 (고정)

        - request.data.session_id 필수
        - subject는 백엔드에서 자동 주입
        """
        session_id = self.request.data.get("session_id")
        if not session_id:
            raise ValidationError({"session_id": "session_id is required"})

        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            raise ValidationError({"session_id": "session_id must be integer"})

        try:
            session = Session.objects.select_related("lecture").get(id=session_id)
        except Session.DoesNotExist:
            raise ValidationError({"session_id": "invalid session_id"})

        serializer.save(
            session=session,
            subject=session.lecture.subject,
        )

    # ======================================================
    # QUERY FILTERS
    # ======================================================
    @staticmethod
    def _has_relation(model, name: str) -> bool:
        """
        model._meta.get_fields() 기반으로 relation/field 존재 여부 검사
        """
        try:
            return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
        except Exception:
            return False

    def get_queryset(self):
        qs = super().get_queryset()

        session_id = self.request.query_params.get("session_id")
        if session_id:
            sid = int(session_id)

            if self._has_relation(Exam, "sessions"):
                qs = qs.filter(sessions__id=sid)
            elif self._has_relation(Exam, "session"):
                qs = qs.filter(session__id=sid)
            else:
                return qs.none()

        lecture_id = self.request.query_params.get("lecture_id")
        if lecture_id:
            lid = int(lecture_id)

            if self._has_relation(Exam, "sessions"):
                qs = qs.filter(sessions__lecture_id=lid)
            elif self._has_relation(Exam, "session"):
                qs = qs.filter(session__lecture_id=lid)
            else:
                return qs.none()

        return qs.distinct().order_by("-created_at")
