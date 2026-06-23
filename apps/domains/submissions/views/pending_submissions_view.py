# PATH: apps/domains/submissions/views/pending_submissions_view.py
from __future__ import annotations

from typing import Any, Dict, Optional

from datetime import timedelta

from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from academy.adapters.db.django import repositories_enrollment as enrollment_repo
from academy.adapters.db.django import repositories_exams as exams_repo
from academy.adapters.db.django import repositories_homework as homework_repo
from academy.adapters.db.django import repositories_submissions as submissions_repo
from apps.core.permissions import TenantResolvedAndStaff


def _get_photo_url(student) -> Optional[str]:
    """R2 presigned URL for student profile photo."""
    if not student:
        return None
    r2_key = getattr(student, "profile_photo_r2_key", None) or ""
    if not r2_key:
        return None
    try:
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

        return generate_presigned_get_url_storage(key=r2_key, expires_in=3600)
    except Exception:
        return None


SUBMISSION_TARGET_EXAM = submissions_repo.target_type_exam()
SUBMISSION_TARGET_HOMEWORK = submissions_repo.target_type_homework()
SUBMISSION_STATUS_DONE = submissions_repo.status_done()
SUBMISSION_STATUS_FAILED = submissions_repo.status_failed()

# Statuses considered "pending" (actively being processed)
PENDING_STATUSES = submissions_repo.pending_statuses()

# Terminal statuses shown only if recent (last 24h)
TERMINAL_STATUSES = [SUBMISSION_STATUS_DONE, SUBMISSION_STATUS_FAILED]


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
            qs = submissions_repo.submission_filter_tenant(tenant).filter(status__in=PENDING_STATUSES)
        else:
            # 'all' (default): pending + terminal from last 24h
            from django.db.models import Q

            cutoff = now - timedelta(hours=24)
            qs = submissions_repo.submission_filter_tenant(tenant).filter(
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
            if s.target_type == SUBMISSION_TARGET_EXAM:
                exam_ids.add(int(s.target_id))
            elif s.target_type == SUBMISSION_TARGET_HOMEWORK:
                homework_ids.add(int(s.target_id))

        # ── Enrollment → student + lecture (batch) ──────────────
        # 🔐 tenant 필터: Submission이 tenant 스코프라도 enrollment_id 자체에는
        # 강제 제약이 없으므로 오염된 row가 다른 tenant의 enrollment를 참조하면
        # 학생 메타가 노출될 수 있다. 명시적으로 tenant 강제.
        enrollment_map: Dict[int, Any] = {}
        if enrollment_ids:
            enrollment_map = enrollment_repo.enrollment_student_map_by_ids(enrollment_ids, tenant=tenant)

        # ── Exam targets → title + session info (batch) ────────
        exam_map: Dict[int, Dict[str, Any]] = {}
        if exam_ids:
            exam_map = exams_repo.exam_target_info_map(exam_ids, tenant=tenant)

        # ── Homework targets → title + session info (batch) ────
        homework_map: Dict[int, Dict[str, Any]] = {}
        if homework_ids:
            homework_map = homework_repo.homework_target_info_map(homework_ids, tenant=tenant)

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
            if s.target_type == SUBMISSION_TARGET_EXAM:
                target_info = exam_map.get(int(s.target_id), {})
            elif s.target_type == SUBMISSION_TARGET_HOMEWORK:
                target_info = homework_map.get(int(s.target_id), {})
            else:
                target_info = {}

            # File type from file_key extension
            file_key = s.file_key or ""
            file_type = ""
            if file_key and "." in file_key:
                file_type = file_key.rsplit(".", 1)[-1].lower()

            # 🚦 target_resolved: Exam/Homework 본체 + 세션/강의 매칭이 모두 살아있는지.
            # 미식별/orphan row 운영자에게 적절한 action(폐기 vs 학생지정 vs 결과보기)을 분기시키는 단일 기준.
            #
            # target_resolved_reason: !target_resolved 일 때 운영자/디버깅용 사유.
            #   target_missing  — Exam/Homework 본체가 없음 (삭제 또는 cross-tenant)
            #   session_missing — Exam/Homework 는 있으나 sessions 매칭 실패
            #   (target_resolved=True 일 때는 None)
            target_title = target_info.get("target_title")
            target_lecture_id = target_info.get("lecture_id")
            target_session_id = target_info.get("session_id")
            target_resolved = bool(target_title and target_lecture_id and target_session_id)
            if target_resolved:
                target_resolved_reason = None
            elif not target_title:
                target_resolved_reason = "target_missing"
            else:
                target_resolved_reason = "session_missing"

            # 🗑 discarded 메타: status=FAILED 중 운영자/시스템 폐기 처리 본 row 식별.
            # 진짜 처리 실패 vs 폐기를 UI 에서 구분하기 위함.
            meta_obj = s.meta or {}
            discarded_meta = meta_obj.get("discarded") if isinstance(meta_obj, dict) else None
            is_discarded = isinstance(discarded_meta, dict)
            discard_reason_value: Optional[str] = (
                str(discarded_meta.get("reason") or "") if is_discarded else None
            ) or None

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
                    "target_resolved": target_resolved,
                    "target_resolved_reason": target_resolved_reason,
                    "is_discarded": is_discarded,
                    "discard_reason": discard_reason_value,
                    "file_key": file_key,
                    "file_type": file_type or s.file_type or "",
                    "file_size": s.file_size,
                    "profile_photo_url": _get_photo_url(student),
                }
            )

        return Response(items, status=200)
