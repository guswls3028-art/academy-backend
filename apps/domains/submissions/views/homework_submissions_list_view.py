# PATH: apps/domains/submissions/views/homework_submissions_list_view.py
from __future__ import annotations

from typing import Any, Dict, Optional

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.submissions.models import Submission


def _map_row_status(s: str) -> str:
    s = str(s or "").lower()
    if s in ("failed",):
        return "failed"
    if s in ("done",):
        return "done"
    if s in ("submitted", "dispatched"):
        return "pending"
    if s in ("extracting", "answers_ready", "grading", "needs_identification"):
        return "processing"
    return "processing"


def _resolve_student_name(enrollment_id: Optional[int]) -> str:
    if not enrollment_id:
        return ""
    try:
        from apps.domains.enrollment.models import Enrollment

        obj = Enrollment.objects.filter(id=int(enrollment_id)).first()
        if obj:
            for attr in ("student_name", "name", "full_name"):
                v = getattr(obj, attr, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass
    return ""


def _resolve_lecture_info(enrollment_id: Optional[int]) -> Dict[str, Any]:
    if not enrollment_id:
        return {}
    try:
        from apps.domains.enrollment.models import Enrollment

        obj = Enrollment.objects.select_related("lecture").filter(id=int(enrollment_id)).first()
        if obj and getattr(obj, "lecture", None):
            lec = obj.lecture
            return {
                "lecture_title": getattr(lec, "title", ""),
                "lecture_color": getattr(lec, "color", None),
            }
    except Exception:
        pass
    return {}


class HomeworkSubmissionsListView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request, homework_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        qs = (
            Submission.objects.filter(
                tenant=tenant,
                target_type=Submission.TargetType.HOMEWORK,
                target_id=int(homework_id),
            )
            .order_by("-id")[:200]
        )

        items: list[Dict[str, Any]] = []
        for s in qs:
            enrollment_id = getattr(s, "enrollment_id", None)
            lecture_info = _resolve_lecture_info(enrollment_id)
            source = getattr(s, "source", "")
            file_key = getattr(s, "file_key", None) or ""
            file_type = ""
            file_size = None
            if file_key:
                ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""
                file_type = ext
            meta = getattr(s, "meta", None) or {}
            if isinstance(meta, dict):
                file_size = meta.get("file_size")

            items.append(
                {
                    "id": int(s.id),
                    "enrollment_id": int(enrollment_id) if enrollment_id else 0,
                    "student_name": _resolve_student_name(enrollment_id),
                    "status": _map_row_status(getattr(s, "status", "")),
                    "source": str(source),
                    "file_key": file_key,
                    "file_type": file_type,
                    "file_size": file_size,
                    "created_at": s.created_at.isoformat() if hasattr(s, "created_at") and s.created_at else None,
                    **lecture_info,
                }
            )

        return Response(items, status=200)
