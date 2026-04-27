# PATH: apps/domains/submissions/views/homework_submissions_list_view.py
from __future__ import annotations

from typing import Any, Dict, Optional

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission


def _resolve_student_name(enrollment_id: Optional[int], tenant) -> str:
    if not enrollment_id or not tenant:
        return ""
    try:
        from apps.domains.enrollment.models import Enrollment
        obj = Enrollment.objects.select_related("student").filter(id=int(enrollment_id), tenant=tenant).first()
        if obj:
            student = getattr(obj, "student", None)
            if student:
                name = getattr(student, "name", None)
                if name and isinstance(name, str) and name.strip():
                    return name.strip()
            for attr in ("student_name", "name", "full_name"):
                v = getattr(obj, attr, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass
    return ""


def _resolve_lecture_info(enrollment_id: Optional[int], tenant) -> Dict[str, Any]:
    if not enrollment_id or not tenant:
        return {}
    try:
        from apps.domains.enrollment.models import Enrollment
        obj = Enrollment.objects.select_related("lecture").filter(id=int(enrollment_id), tenant=tenant).first()
        if obj and getattr(obj, "lecture", None):
            lec = obj.lecture
            return {
                "lecture_title": getattr(lec, "title", ""),
                "lecture_color": getattr(lec, "color", None),
                "lecture_chip_label": getattr(lec, "chip_label", None),
            }
    except Exception:
        pass
    return {}


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
        return create_presigned_get_url(r2_key, expires_in=3600, bucket=settings.R2_STORAGE_BUCKET)
    except Exception:
        return None


class HomeworkSubmissionsListView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, homework_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        # 테넌트 격리: homework가 해당 테넌트 소속인지 검증
        from apps.domains.homework_results.models import Homework
        if not Homework.objects.filter(
            id=int(homework_id),
            session__lecture__tenant=tenant,
        ).exists():
            return Response([], status=200)

        qs = (
            Submission.objects.filter(
                tenant=tenant,
                target_type=Submission.TargetType.HOMEWORK,
                target_id=int(homework_id),
            )
            .order_by("-id")[:200]
        )

        # ✅ 클리닉 하이라이트 일괄 계산
        submissions_list = list(qs)
        enrollment_ids = set()
        for s in submissions_list:
            eid = getattr(s, "enrollment_id", None)
            if eid:
                enrollment_ids.add(int(eid))

        from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map
        highlight_map = compute_clinic_highlight_map(
            tenant=tenant,
            enrollment_ids=enrollment_ids,
        ) if enrollment_ids else {}

        # enrollment_id → (student, lecture) 일괄 조회
        # 🔐 tenant 강제: Submission tenant 스코프와 무관하게 enrollment_id 참조 자체에는
        # 강제 제약이 없으므로 오염 시 다른 tenant 학생 노출 위험 → 명시적으로 차단.
        enrollment_map: Dict[int, Any] = {}
        if enrollment_ids:
            from apps.domains.enrollment.models import Enrollment
            for enr in Enrollment.objects.select_related("student", "lecture").filter(id__in=enrollment_ids, tenant=tenant):
                enrollment_map[enr.id] = enr

        items: list[Dict[str, Any]] = []
        for s in submissions_list:
            enrollment_id = getattr(s, "enrollment_id", None)
            enrollment = enrollment_map.get(int(enrollment_id)) if enrollment_id else None
            student = getattr(enrollment, "student", None) if enrollment else None
            lecture = getattr(enrollment, "lecture", None) if enrollment else None

            # student name
            student_name = ""
            if student:
                student_name = getattr(student, "name", "") or ""
            if not student_name:
                student_name = _resolve_student_name(enrollment_id, tenant)

            # lecture info
            if lecture:
                lecture_info = {
                    "lecture_title": getattr(lecture, "title", ""),
                    "lecture_color": getattr(lecture, "color", None),
                    "lecture_chip_label": getattr(lecture, "chip_label", None),
                }
            else:
                lecture_info = _resolve_lecture_info(enrollment_id, tenant)

            source = getattr(s, "source", "")
            file_key = getattr(s, "file_key", None) or ""
            file_type = ""
            file_size = getattr(s, "file_size", None)
            if file_key:
                ext = file_key.rsplit(".", 1)[-1].lower() if "." in file_key else ""
                file_type = ext

            student_id = int(getattr(student, "id", 0)) if student else 0
            student_phone_v = getattr(student, "phone", "") if student else ""
            parent_phone_v = getattr(student, "parent_phone", "") if student else ""

            items.append(
                {
                    "id": int(s.id),
                    "enrollment_id": int(enrollment_id) if enrollment_id else 0,
                    "student_id": student_id,
                    "student_name": student_name,
                    "student_phone": student_phone_v or None,
                    "parent_phone": parent_phone_v or None,
                    "status": str(getattr(s, "status", "")),
                    "source": str(source),
                    "file_key": file_key,
                    "file_type": file_type,
                    "file_size": file_size,
                    "created_at": s.created_at.isoformat() if hasattr(s, "created_at") and s.created_at else None,
                    "profile_photo_url": _get_photo_url(student),
                    "name_highlight_clinic_target": highlight_map.get(int(enrollment_id), False) if enrollment_id else False,
                    **lecture_info,
                }
            )

        return Response(items, status=200)
