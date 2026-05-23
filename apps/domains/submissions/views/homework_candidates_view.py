# PATH: apps/domains/submissions/views/homework_candidates_view.py
"""
Homework 검토 학생 picker 전용 엔드포인트.

GET /api/v1/submissions/submissions/homework/<homework_id>/candidates/?q=<query>

- 해당 과제의 SessionEnrollment 기반 학생 리스트.
- q (검색어): 학생명, 학생폰 뒤 8자리, 학부모폰 뒤 8자리 부분일치.
- 최대 50건.
- 응답 필드: enrollment_id, student_name, student_phone_last4,
             parent_phone_last4, lecture_title, already_matched.

Tenant isolation: homework.tenant 강제.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.homework_results.models import Homework
from apps.domains.submissions.models import Submission


def _mask_phone_tail(phone: str | None) -> str:
    p = str(phone or "").replace("-", "").strip()
    if len(p) < 4:
        return ""
    return p[-4:]


class HomeworkCandidatesView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, homework_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        hw = Homework.objects.filter(id=int(homework_id), tenant=tenant).first()
        if not hw:
            return Response({"detail": "과제를 찾을 수 없습니다."}, status=404)
        if isinstance(hw.meta, dict) and hw.meta.get("removed_from_session_at"):
            return Response([], status=200)

        q = str(request.query_params.get("q") or "").strip()

        # session 의 SessionEnrollment 기반
        session = hw.session
        if not session:
            return Response([], status=200)

        enrollment_ids = list(
            SessionEnrollment.objects
            .filter(session_id=session.id)
            .filter(enrollment__status="ACTIVE")
            .values_list("enrollment_id", flat=True)
            .distinct()
        )

        if not enrollment_ids:
            return Response([], status=200)

        qs = (
            Enrollment.objects
            .filter(id__in=enrollment_ids, tenant=tenant)
            .filter(student__deleted_at__isnull=True)
            .select_related("student", "lecture")
        )

        if q:
            digits = "".join(ch for ch in q if ch.isdigit())
            name_q = Q(student__name__icontains=q)
            phone_q = Q()
            if digits and len(digits) >= 3:
                phone_q = (
                    Q(student__phone__icontains=digits)
                    | Q(student__parent_phone__icontains=digits)
                )
            qs = qs.filter(name_q | phone_q) if phone_q.children else qs.filter(name_q)

        qs = qs.order_by("student__name", "id")[:50]

        # 이미 submission 매칭된 enrollment_id
        matched_ids: Set[int] = set(
            Submission.objects
            .filter(
                tenant=tenant,
                target_type=Submission.TargetType.HOMEWORK,
                target_id=int(homework_id),
                enrollment_id__isnull=False,
            )
            .exclude(enrollment_id=0)
            .values_list("enrollment_id", flat=True)
        )

        items: List[Dict[str, Any]] = []
        for e in qs:
            student = getattr(e, "student", None)
            lecture = getattr(e, "lecture", None)
            student_name = str(getattr(student, "name", "") or "") if student else ""
            student_phone = str(getattr(student, "phone", "") or "") if student else ""
            parent_phone = str(getattr(student, "parent_phone", "") or "") if student else ""
            lecture_title = str(getattr(lecture, "title", "") or "") if lecture else ""

            items.append({
                "enrollment_id": int(e.id),
                "student_name": student_name,
                "student_phone_last4": _mask_phone_tail(student_phone),
                "parent_phone_last4": _mask_phone_tail(parent_phone),
                "lecture_title": lecture_title or None,
                "already_matched": int(e.id) in matched_ids,
            })

        return Response(items, status=200)
