# PATH: apps/domains/submissions/views/pending_submissions_view.py
from __future__ import annotations

from typing import Any, Dict, Optional

from datetime import timedelta

from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission


def _get_photo_url(student) -> Optional[str]:
    """R2 presigned URL for student profile photo."""
    if not student:
        return None
    r2_key = getattr(student, "profile_photo_r2_key", None) or ""
    if not r2_key:
        return None
    try:
        from django.conf import settings
        from libs.r2_client.presign import create_presigned_get_url

        return create_presigned_get_url(
            r2_key, expires_in=3600, bucket=settings.R2_STORAGE_BUCKET
        )
    except Exception:
        return None


# Statuses considered "pending" (actively being processed)
PENDING_STATUSES = [
    "submitted",
    "dispatched",
    "extracting",
    "needs_identification",
    "answers_ready",
    "grading",
]

# Terminal statuses shown only if recent (last 24h)
TERMINAL_STATUSES = ["done", "failed"]


class PendingSubmissionsView(APIView):
    """
    GET /api/v1/submissions/submissions/pending/

    Admin submissions inbox — lists pending/recent submissions with resolved
    context (student name, target title, lecture/session info).

    Query params:
      ?filter=pending  — only actively-processing submissions
      ?filter=all      — pending + terminal (done/failed) from last 24h
      (default)        — same as 'all'
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        filter_mode = request.query_params.get("filter", "all")
        now = timezone.now()

        # ── Build queryset ──────────────────────────────────────
        if filter_mode == "pending":
            qs = Submission.objects.filter(
                tenant=tenant,
                status__in=PENDING_STATUSES,
            )
        else:
            # 'all' (default): pending + terminal from last 24h
            from django.db.models import Q

            cutoff = now - timedelta(hours=24)
            qs = Submission.objects.filter(
                tenant=tenant,
            ).filter(
                Q(status__in=PENDING_STATUSES)
                | Q(status__in=TERMINAL_STATUSES, created_at__gte=cutoff)
            )

        submissions = list(qs.order_by("-created_at")[:200])
        if not submissions:
            return Response([], status=200)

        # ── Batch-collect IDs for efficient lookups ─────────────
        enrollment_ids: set[int] = set()
        exam_ids: set[int] = set()
        homework_ids: set[int] = set()

        for s in submissions:
            eid = s.enrollment_id
            if eid:
                enrollment_ids.add(int(eid))
            if s.target_type == Submission.TargetType.EXAM:
                exam_ids.add(int(s.target_id))
            elif s.target_type == Submission.TargetType.HOMEWORK:
                homework_ids.add(int(s.target_id))

        # ── Enrollment → student + lecture (batch) ──────────────
        # 🔐 tenant 필터: Submission이 tenant 스코프라도 enrollment_id 자체에는
        # 강제 제약이 없으므로 오염된 row가 다른 tenant의 enrollment를 참조하면
        # 학생 메타가 노출될 수 있다. 명시적으로 tenant 강제.
        enrollment_map: Dict[int, Any] = {}
        if enrollment_ids:
            from apps.domains.enrollment.models import Enrollment

            for enr in (
                Enrollment.objects.select_related("student", "lecture")
                .filter(id__in=enrollment_ids, tenant=tenant)
            ):
                enrollment_map[enr.id] = enr

        # ── Exam targets → title + session info (batch) ────────
        exam_map: Dict[int, Dict[str, Any]] = {}
        if exam_ids:
            from apps.domains.exams.models import Exam

            exams = (
                Exam.objects.filter(id__in=exam_ids, tenant=tenant)
                .prefetch_related("sessions__lecture")
            )
            for exam in exams:
                session = exam.sessions.first()
                exam_map[exam.id] = {
                    "target_title": exam.title,
                    "session_id": session.id if session else None,
                    "lecture_id": session.lecture_id if session else None,
                    "lecture_title": (
                        session.lecture.title
                        if session and getattr(session, "lecture", None)
                        else ""
                    ),
                }

        # ── Homework targets → title + session info (batch) ────
        homework_map: Dict[int, Dict[str, Any]] = {}
        if homework_ids:
            from apps.domains.homework_results.models import Homework

            homeworks = (
                Homework.objects.filter(id__in=homework_ids, tenant=tenant)
                .select_related("session__lecture")
            )
            for hw in homeworks:
                session = hw.session
                homework_map[hw.id] = {
                    "target_title": hw.title,
                    "session_id": session.id if session else None,
                    "lecture_id": (
                        session.lecture_id if session else None
                    ),
                    "lecture_title": (
                        session.lecture.title
                        if session and getattr(session, "lecture", None)
                        else ""
                    ),
                }

        # ── Build response items ────────────────────────────────
        items: list[Dict[str, Any]] = []
        for s in submissions:
            enrollment_id = s.enrollment_id
            enrollment = (
                enrollment_map.get(int(enrollment_id))
                if enrollment_id
                else None
            )
            student = (
                getattr(enrollment, "student", None) if enrollment else None
            )

            # Student name
            student_name = ""
            if student:
                student_name = getattr(student, "name", "") or ""

            # Target info (title, session, lecture)
            if s.target_type == Submission.TargetType.EXAM:
                target_info = exam_map.get(int(s.target_id), {})
            elif s.target_type == Submission.TargetType.HOMEWORK:
                target_info = homework_map.get(int(s.target_id), {})
            else:
                target_info = {}

            # File type from file_key extension
            file_key = s.file_key or ""
            file_type = ""
            if file_key and "." in file_key:
                file_type = file_key.rsplit(".", 1)[-1].lower()

            items.append(
                {
                    "id": s.id,
                    "enrollment_id": int(enrollment_id) if enrollment_id else 0,
                    "target_type": s.target_type,
                    "target_id": s.target_id,
                    "source": s.source,
                    "status": s.status,
                    "created_at": (
                        s.created_at.isoformat()
                        if s.created_at
                        else None
                    ),
                    "student_name": student_name,
                    "target_title": target_info.get("target_title", ""),
                    "lecture_id": target_info.get("lecture_id"),
                    "lecture_title": target_info.get("lecture_title", ""),
                    "session_id": target_info.get("session_id"),
                    "file_key": file_key,
                    "file_type": file_type or s.file_type or "",
                    "file_size": s.file_size,
                    "profile_photo_url": _get_photo_url(student),
                }
            )

        return Response(items, status=200)
