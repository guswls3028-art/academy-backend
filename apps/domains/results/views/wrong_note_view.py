# apps/domains/results/views/wrong_note_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from apps.domains.results.permissions import is_teacher_user
from apps.domains.enrollment.models import Enrollment

from apps.domains.results.models import ResultFact
from apps.domains.exams.models import Exam


class WrongNoteView(APIView):
    """
    오답노트 조회 API (v1)

    🔴 보안 패치 요약
    - enrollment_id를 query로 받기 때문에 접근 제어 필수
    - 학생: 본인 enrollment만 허용
    - 교사/관리자: 전체 허용

    ✅ 운영 안정성 패치 (Critical #3)
    - Exam ↔ Session reverse related_name이 프로젝트마다 다를 수 있음
      (session / sessions / session_set 등)
    - 기존 코드의 session__order 가정은 깨질 수 있으므로
      Exam 모델의 실제 relation 이름을 검사 후 필터 적용
    """

    permission_classes = [IsAuthenticated]

    # --------------------------------------------------
    # 🔐 enrollment 접근 권한 검사 (핵심 보안 로직)
    # --------------------------------------------------
    def _assert_enrollment_access(self, request, enrollment_id: int) -> None:
        user = request.user

        if is_teacher_user(user):
            return

        qs = Enrollment.objects.filter(id=int(enrollment_id))

        if hasattr(Enrollment, "user_id"):
            qs = qs.filter(user_id=user.id)
        elif hasattr(Enrollment, "student_id"):
            qs = qs.filter(student_id=user.id)

        if not qs.exists():
            raise PermissionDenied("You cannot access this enrollment_id.")

    @staticmethod
    def _has_relation(model, name: str) -> bool:
        """
        Exam 모델에 session/sessions 관계가 존재하는지 검사 (정석).
        """
        try:
            return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
        except Exception:
            return False

    def get(self, request):
        """
        Query Params
        - enrollment_id (required)
        - lecture_id (optional)
        - exam_id (optional)
        - from_session_order (optional, default=2)
        """

        enrollment_id = request.query_params.get("enrollment_id")
        if not enrollment_id:
            return Response({"detail": "enrollment_id is required"}, status=400)

        self._assert_enrollment_access(request, int(enrollment_id))

        lecture_id = request.query_params.get("lecture_id")
        exam_id = request.query_params.get("exam_id")
        from_order = int(request.query_params.get("from_session_order", 2))

        qs = ResultFact.objects.filter(
            enrollment_id=int(enrollment_id),
            is_correct=False,
            target_type="exam",
        )

        if exam_id:
            qs = qs.filter(target_id=int(exam_id))

        if lecture_id:
            # ----------------------------------------------------------
            # ✅ Critical #3 PATCH:
            # - Exam ↔ Session 관계명이 session/sessions인지 검사 후 적용
            # - 둘 다 없으면 안전하게 none()
            # ----------------------------------------------------------
            exam_qs = Exam.objects.filter(lecture_id=int(lecture_id))

            if self._has_relation(Exam, "sessions"):
                exam_qs = exam_qs.filter(sessions__order__gte=from_order)
            elif self._has_relation(Exam, "session"):
                exam_qs = exam_qs.filter(session__order__gte=from_order)
            else:
                exam_qs = exam_qs.none()

            exam_ids = exam_qs.values_list("id", flat=True)
            qs = qs.filter(target_id__in=list(exam_ids))

        qs = qs.order_by("target_id", "question_id")

        items = [{
            "exam_id": f.target_id,
            "question_id": f.question_id,
            "answer": f.answer,
            "score": f.score,
            "max_score": f.max_score,
            "source": f.source,
            "meta": f.meta,
            "created_at": f.created_at,
        } for f in qs]

        return Response({
            "enrollment_id": int(enrollment_id),
            "count": len(items),
            "items": items,
        })
