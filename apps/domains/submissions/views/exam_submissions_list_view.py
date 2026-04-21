# PATH: apps/domains/submissions/views/exam_submissions_list_view.py
from __future__ import annotations

from typing import Any, Dict, Optional

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.submissions.models import Submission


def _resolve_student_name(submission) -> str:
    """
    Submission → enrollment → student name 추출 (select_related 전제).
    """
    enrollment = getattr(submission, "_enrollment_cache", None)
    if enrollment is None:
        enrollment_id = getattr(submission, "enrollment_id", None)
        if not enrollment_id:
            return ""
        try:
            from apps.domains.enrollment.models import Enrollment
            enrollment = Enrollment.objects.select_related("student").filter(id=int(enrollment_id)).first()
        except Exception:
            return ""
    if not enrollment:
        return ""
    student = getattr(enrollment, "student", None)
    if student:
        name = getattr(student, "name", None)
        if name and isinstance(name, str) and name.strip():
            return name.strip()
    for attr in ("student_name", "name", "full_name"):
        v = getattr(enrollment, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _resolve_score_for_submission(submission_id: int) -> Optional[float]:
    try:
        from apps.domains.results.models import Result
        r = Result.objects.filter(submission_id=int(submission_id)).order_by("-id").first()
        if r and getattr(r, "score", None) is not None:
            return float(r.score)
    except Exception:
        pass
    return None


class ExamSubmissionsListView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request, exam_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        # 테넌트 격리: exam이 해당 테넌트 소속인지 검증
        from apps.domains.exams.models import Exam
        if not Exam.objects.filter(
            id=int(exam_id),
        ).filter(
            sessions__lecture__tenant=tenant,
        ).exists():
            return Response([], status=200)

        qs = (
            Submission.objects
            .filter(
                tenant=tenant,
                target_type=Submission.TargetType.EXAM,
                target_id=int(exam_id),
            )
            .order_by("-id")[:200]
        )

        items: list[Dict[str, Any]] = []
        for s in qs:
            enrollment_id = getattr(s, "enrollment_id", None)
            s_meta = s.meta or {}
            mr = s_meta.get("manual_review") if isinstance(s_meta, dict) else None

            items.append(
                {
                    "id": int(s.id),
                    "enrollment_id": int(enrollment_id) if enrollment_id else 0,
                    "student_name": _resolve_student_name(s),
                    "status": str(getattr(s, "status", "")),
                    "source": str(getattr(s, "source", "")),
                    "score": _resolve_score_for_submission(int(s.id)),
                    "created_at": s.created_at.isoformat(),
                    "file_key": getattr(s, "file_key", None) or "",
                    "has_file": bool(getattr(s, "file_key", None)),
                    "manual_review_required": bool(
                        isinstance(mr, dict) and mr.get("required")
                    ),
                    "manual_review_reasons": (
                        list(mr.get("reasons") or []) if isinstance(mr, dict) else []
                    ),
                    "identifier_status": s_meta.get("identifier_status")
                    if isinstance(s_meta, dict)
                    else None,
                }
            )

        return Response(items, status=200)
