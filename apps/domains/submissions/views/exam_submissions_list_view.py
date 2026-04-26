# PATH: apps/domains/submissions/views/exam_submissions_list_view.py
from __future__ import annotations

from typing import Any, Dict

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission


def _extract_name_from_enrollment(enrollment) -> str:
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


class ExamSubmissionsListView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

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

        qs = list(
            Submission.objects
            .filter(
                tenant=tenant,
                target_type=Submission.TargetType.EXAM,
                target_id=int(exam_id),
            )
            .order_by("-id")[:200]
        )

        # N+1 방지: enrollment/result 를 bulk 조회해 dict 로 lookup.
        # 과거에는 행마다 Enrollment.filter / Result.filter 로 2회씩 쿼리가 발생해
        # 200행 기준 최대 400 쿼리. 현재는 최대 2쿼리로 고정.
        # 🔐 tenant 필터: Submission이 tenant 스코프여도 enrollment_id 참조는 강제 제약이
        # 없으므로 오염 시 다른 tenant 학생 메타가 노출될 수 있다. 명시적으로 tenant 강제.
        enrollment_map: Dict[int, Any] = {}
        enrollment_ids = {int(s.enrollment_id) for s in qs if getattr(s, "enrollment_id", None)}
        if enrollment_ids:
            from apps.domains.enrollment.models import Enrollment
            for e in Enrollment.objects.select_related("student").filter(id__in=enrollment_ids, tenant=tenant):
                enrollment_map[int(e.id)] = e

        score_map: Dict[int, float] = {}
        submission_ids = [int(s.id) for s in qs]
        if submission_ids:
            from apps.domains.results.models import Result
            # submission 당 최신 Result 1건만 필요 — id desc 순회하며 첫 등장만 채택.
            for r in Result.objects.filter(submission_id__in=submission_ids).order_by("-id").only(
                "id", "submission_id", "score"
            ):
                sid = int(r.submission_id)
                if sid not in score_map and getattr(r, "score", None) is not None:
                    score_map[sid] = float(r.score)

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

            enrollment = enrollment_map.get(int(enrollment_id)) if enrollment_id else None

            items.append(
                {
                    "id": int(s.id),
                    "enrollment_id": int(enrollment_id) if enrollment_id else 0,
                    "student_name": _extract_name_from_enrollment(enrollment),
                    "status": str(getattr(s, "status", "")),
                    "source": str(getattr(s, "source", "")),
                    "score": score_map.get(int(s.id)),
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
