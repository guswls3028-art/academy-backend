# PATH: apps/domains/submissions/views/exam_candidates_view.py
"""
OMR 검토 학생 picker 전용 엔드포인트.

GET /api/v1/submissions/submissions/exams/<exam_id>/candidates/?q=<query>

- 해당 시험의 ExamEnrollment을 기반으로 응시 대상 학생 리스트 반환.
- ExamEnrollment이 비어있으면 exam.sessions의 SessionEnrollment로 fallback.
- q (검색어): 학생명, 학생폰 뒤 8자리, 학부모폰 뒤 8자리 부분일치.
- 최대 50건.
- 응답 필드: enrollment_id, student_name, student_phone, parent_phone,
             lecture_title, already_matched (기존 submission과 매칭된 학생인지).
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.submissions.models import Submission


def _mask_phone_tail(phone: str | None) -> str:
    p = str(phone or "").replace("-", "").strip()
    if len(p) < 4:
        return ""
    return p[-4:]


class ExamCandidatesView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        exam = Exam.objects.filter(
            id=int(exam_id),
            sessions__lecture__tenant=tenant,
        ).first()
        if not exam:
            return Response({"detail": "시험을 찾을 수 없습니다."}, status=404)

        q = str(request.query_params.get("q") or "").strip()

        # 1순위: ExamEnrollment (응시 대상자)
        enrollment_ids = list(
            ExamEnrollment.objects
            .filter(exam_id=int(exam_id))
            .values_list("enrollment_id", flat=True)
        )

        # fallback: ExamEnrollment이 비어있으면 exam.sessions의 SessionEnrollment
        if not enrollment_ids:
            session_ids = list(exam.sessions.values_list("id", flat=True))
            enrollment_ids = list(
                SessionEnrollment.objects
                .filter(session_id__in=session_ids)
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
            # 숫자만 남기기 (phone 뒤 8자리 검색용)
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
                target_type=Submission.TargetType.EXAM,
                target_id=int(exam_id),
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
