# PATH: apps/domains/exams/views/exam_view.py

from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError


from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer


class ExamViewSet(ModelViewSet):
    """
    ✅ SaaS 표준 Exam 조회 API

    지원:
    - GET /exams/?session_id=123
    - GET /exams/?lecture_id=10

    🔧 운영 안정성 패치 (Critical)
    - 기존 try/except 방식은 Django ORM의 filter()가 "필드 없음"을
      try/except로 안정적으로 잡아주지 않는 케이스가 있어 실제로 안전하지 않다.
    - 따라서 Exam 모델의 _meta.get_fields()로 관계 필드명을 먼저 검사 후,
      존재하는 relation으로만 filter를 건다.
    - 둘 다 없으면 qs.none()으로 안전하게 빈 결과를 반환한다.
    """

    queryset = Exam.objects.all()
    serializer_class = ExamSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        """
        Exam 생성 시 subject 자동 주입
        - 프론트에서 subject 받지 않음
        - session → lecture → subject 기준으로 결정
        """
        session = serializer.validated_data.get("session")
        if not session:
            raise ValidationError({"session": "session is required to create exam"})

        serializer.save(
            subject=session.lecture.subject
        )

    @staticmethod
    def _has_relation(model, name: str) -> bool:
        """
        model._meta.get_fields() 기반으로 relation/field 존재 여부 검사.

        ✅ 이유:
        - 잘못된 related_name을 filter에 넣으면
          "예외가 안 나고 조용히 무시" 같은 상황이 아니라
          런타임에서 다른 형태로 깨질 수 있어 운영에 위험.
        - 필드가 존재하는지 먼저 확정하고 filter 적용하는 게 정석.
        """
        try:
            return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
        except Exception:
            # _meta 접근 자체가 문제인 경우는 거의 없지만,
            # 운영 안전성을 위해 False 처리
            return False

    def get_queryset(self):
        qs = super().get_queryset()

        session_id = self.request.query_params.get("session_id")
        if session_id:
            # session_id는 숫자여야 함
            sid = int(session_id)

            # ✅ 관계명 우선순위: projects마다 다를 수 있으나 보통 sessions가 더 흔함
            if self._has_relation(Exam, "sessions"):
                qs = qs.filter(sessions__id=sid)
            elif self._has_relation(Exam, "session"):
                qs = qs.filter(session__id=sid)
            else:
                # 관계가 불명확하면 안전하게 빈 결과
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
