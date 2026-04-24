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

        # 테넌트 격리: exam이 해당 테넌트 소속이거나, 혹은 tenant 소속 submission이
        # 최소 1건 있으면 노출. 세션 연결만으로 필터링할 경우 session에서 떼어진(고아)
        # exam은 submission이 있어도 리스트가 비어 운영자가 존재 자체를 모름.
        # 아래 queryset에서 tenant=tenant로 최종 스코프를 거므로 격리는 유지된다.
        from apps.domains.exams.models import Exam
        exam_q = Exam.objects.filter(id=int(exam_id))
        exam_allowed = exam_q.filter(sessions__lecture__tenant=tenant).exists()
        if not exam_allowed and hasattr(Exam, "tenant"):
            exam_allowed = exam_q.filter(tenant=tenant).exists()
        if not exam_allowed:
            if not Submission.objects.filter(
                tenant=tenant,
                target_type=Submission.TargetType.EXAM,
                target_id=int(exam_id),
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
            ai_result = s_meta.get("ai_result") if isinstance(s_meta, dict) else None
            ai_result_dict = ai_result.get("result") if isinstance(ai_result, dict) else None
            stats = s_meta.get("answer_stats") if isinstance(s_meta, dict) else None

            alignment_method = None
            aligned_flag = None
            sheet_version = None
            if isinstance(ai_result_dict, dict):
                alignment_method = ai_result_dict.get("alignment_method")
                aligned_flag = ai_result_dict.get("aligned")
                sheet_version = ai_result_dict.get("version")

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
                    # 자동채점 진단 필드 (운영자 가시성용)
                    "answer_stats": stats if isinstance(stats, dict) else None,
                    "aligned": aligned_flag,
                    "alignment_method": alignment_method,
                    "sheet_version": sheet_version,
                }
            )

        return Response(items, status=200)
