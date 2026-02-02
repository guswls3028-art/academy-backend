# PATH: apps/domains/submissions/views/exam_submissions_list_view.py
from __future__ import annotations

from typing import Any, Dict, Optional

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.submissions.models import Submission


def _map_row_status(s: str) -> str:
    """
    Front SubmissionRow.status contract:
    - pending | processing | done | failed
    """
    s = str(s or "").lower()

    if s in ("failed",):
        return "failed"

    # needs_identification도 운영상 "처리중(수동개입 필요)"로 본다
    if s in ("done",):
        return "done"

    if s in ("submitted", "dispatched"):
        return "pending"

    if s in ("extracting", "answers_ready", "grading", "needs_identification"):
        return "processing"

    return "processing"


def _resolve_student_name(enrollment_id: Optional[int]) -> str:
    """
    프로젝트마다 Enrollment/Student 모델 경로가 다르므로 best-effort.
    없으면 빈 문자열 반환(프론트가 #id로도 식별 가능).
    """
    if not enrollment_id:
        return ""

    # 1) 가장 흔한 케이스: apps.domains.enrollments.models.Enrollment
    try:
        from apps.domains.enrollments.models import Enrollment  # type: ignore

        obj = Enrollment.objects.filter(id=int(enrollment_id)).first()
        if obj:
            for attr in ("student_name", "name", "full_name"):
                v = getattr(obj, attr, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass

    # 2) 다른 케이스들(있으면 확장)
    return ""


def _resolve_score_for_submission(submission_id: int) -> Optional[float]:
    """
    결과 도메인 구현이 여러 형태일 수 있어 best-effort.
    있으면 score 반환, 없으면 None.
    """
    # 흔한 케이스: results 모델에 submission_id 연결
    try:
        from apps.domains.results.models import Result  # type: ignore

        r = Result.objects.filter(submission_id=int(submission_id)).order_by("-id").first()
        if r and getattr(r, "score", None) is not None:
            return float(r.score)
    except Exception:
        pass

    # 다른 테이블 구조라면 여기 추가
    return None


class ExamSubmissionsListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        qs = (
            Submission.objects
            .filter(target_type=Submission.TargetType.EXAM, target_id=int(exam_id))
            .order_by("-id")[:200]
        )

        items: list[Dict[str, Any]] = []
        for s in qs:
            enrollment_id = getattr(s, "enrollment_id", None)
            items.append(
                {
                    "id": int(s.id),
                    "enrollment_id": int(enrollment_id) if enrollment_id else 0,
                    "student_name": _resolve_student_name(enrollment_id),
                    "status": _map_row_status(getattr(s, "status", "")),
                    "score": _resolve_score_for_submission(int(s.id)),
                    "created_at": s.created_at.isoformat(),
                }
            )

        return Response(items, status=200)
