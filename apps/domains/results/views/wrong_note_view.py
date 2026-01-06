# apps/domains/results/views/wrong_note_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from apps.domains.results.permissions import is_teacher_user
from apps.domains.enrollment.models import Enrollment

from apps.domains.results.serializers.wrong_note_serializers import (
    WrongNoteListResponseSerializer,
)
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    list_wrong_notes_for_enrollment,
)


class WrongNoteView(APIView):
    """
    오답노트 조회 API

    ✅ STEP 3-3 고정:
    - lecture_id/from_session_order 필터는 Service 단일 진실
    - View는 보안 + query parsing + serializer만 담당
    """

    permission_classes = [IsAuthenticated]

    def _assert_enrollment_access(self, request, enrollment_id: int) -> None:
        user = request.user

        if is_teacher_user(user):
            return

        qs = Enrollment.objects.filter(id=int(enrollment_id))

        if hasattr(Enrollment, "user_id"):
            qs = qs.filter(user_id=user.id)
        elif hasattr(Enrollment, "student_id"):
            qs = qs.filter(student_id=user.id)

        if not qs.exists():
            raise PermissionDenied("You cannot access this enrollment_id.")

    def get(self, request):
        """
        Query Params
        - enrollment_id (required)
        - exam_id (optional)
        - lecture_id (optional)
        - from_session_order (optional, default=2)
        - offset (optional, default=0)
        - limit (optional, default=50)
        """
        enrollment_id = request.query_params.get("enrollment_id")
        if not enrollment_id:
            return Response({"detail": "enrollment_id is required"}, status=400)

        enrollment_id_i = int(enrollment_id)
        self._assert_enrollment_access(request, enrollment_id_i)

        exam_id = request.query_params.get("exam_id")
        lecture_id = request.query_params.get("lecture_id")
        from_order = int(request.query_params.get("from_session_order", 2))

        offset = int(request.query_params.get("offset", 0))
        limit = int(request.query_params.get("limit", 50))

        q = WrongNoteQuery(
            exam_id=int(exam_id) if exam_id else None,
            lecture_id=int(lecture_id) if lecture_id else None,
            from_session_order=from_order,
            offset=offset,
            limit=limit,
        )

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=enrollment_id_i,
            q=q,
        )

        next_offset = (offset + limit) if (offset + limit) < total else None
        prev_offset = (offset - limit) if (offset - limit) >= 0 else None

        payload = {
            "count": int(total),
            "next": next_offset,
            "prev": prev_offset,
            "results": items,
        }

        return Response(WrongNoteListResponseSerializer(payload).data)
