# apps/domains/results/views/wrong_note_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.permissions import TenantResolvedAndStaff

from apps.domains.results.serializers.wrong_note_serializers import (
    WrongNoteListResponseSerializer,
)
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    build_wrong_note_source_fingerprint,
    list_wrong_notes_for_enrollment,
)
from apps.support.results.admin_exam_dependencies import (
    enrollment_exists_for_tenant,
    get_enrollment_for_tenant,
)


class WrongNoteView(APIView):
    """
    오답노트 조회 API

    ✅ STEP 3-3 고정:
    - lecture_id/from_session_order/to_session_order 필터는 Service 단일 진실
    - View는 보안 + query parsing + serializer만 담당
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def _assert_enrollment_access(self, request, enrollment_id: int) -> None:
        if not enrollment_exists_for_tenant(enrollment_id=int(enrollment_id), tenant=request.tenant):
            raise PermissionDenied("You cannot access this enrollment_id.")

    def get(self, request):
        """
        Query Params
        - enrollment_id (required)
        - exam_id (optional)
        - lecture_id (optional)
        - from_session_order (optional, default=2)
        - to_session_order (optional, inclusive)
        - offset (optional, default=0)
        - limit (optional, default=50)
        """
        enrollment_id = request.query_params.get("enrollment_id")
        if not enrollment_id:
            return Response({"detail": "enrollment_id is required"}, status=400)

        try:
            enrollment_id_i = int(enrollment_id)
            exam_id_i = (
                int(request.query_params.get("exam_id"))
                if request.query_params.get("exam_id")
                else None
            )
            requested_lecture_id = (
                int(request.query_params.get("lecture_id"))
                if request.query_params.get("lecture_id")
                else None
            )
            from_order = int(request.query_params.get("from_session_order", 2))
            to_session_order = request.query_params.get("to_session_order")
            to_order = (
                int(to_session_order)
                if to_session_order not in (None, "")
                else None
            )
            offset = int(request.query_params.get("offset", 0))
            limit = min(int(request.query_params.get("limit", 50)), 200)
            if (
                enrollment_id_i < 1
                or (exam_id_i is not None and exam_id_i < 1)
                or (requested_lecture_id is not None and requested_lecture_id < 1)
                or from_order < 1
                or (to_order is not None and to_order < from_order)
                or offset < 0
                or limit < 1
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError(
                {"detail": "조회 범위와 페이지 값을 다시 확인해 주세요."}
            )

        self._assert_enrollment_access(request, enrollment_id_i)

        enrollment = get_enrollment_for_tenant(
            enrollment_id=enrollment_id_i,
            tenant=request.tenant,
        )
        if enrollment is None:
            raise PermissionDenied("You cannot access this enrollment_id.")
        if (
            requested_lecture_id is not None
            and requested_lecture_id != int(enrollment.lecture_id)
        ):
            raise ValidationError(
                {"lecture_id": "수강 중인 강의의 오답만 모을 수 있습니다."}
            )

        # 수강 강의를 메타데이터 범위로 고정한다. 단일 시험 조회에서는
        # service가 lecture 주차 필터를 적용하지 않아 1주차도 빠지지 않는다.
        lecture_id_i = int(requested_lecture_id or enrollment.lecture_id)

        q = WrongNoteQuery(
            exam_id=exam_id_i,
            lecture_id=lecture_id_i,
            from_session_order=from_order,
            to_session_order=to_order,
            offset=offset,
            limit=limit,
        )

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=enrollment_id_i,
            q=q,
        )

        fingerprint_items = items
        if offset != 0 or total > len(items):
            _, fingerprint_items = list_wrong_notes_for_enrollment(
                enrollment_id=enrollment_id_i,
                q=WrongNoteQuery(
                    exam_id=exam_id_i,
                    lecture_id=lecture_id_i,
                    from_session_order=from_order,
                    to_session_order=to_order,
                    offset=0,
                    limit=200,
                ),
            )

        next_offset = (offset + limit) if (offset + limit) < total else None
        prev_offset = (offset - limit) if (offset - limit) >= 0 else None

        payload = {
            "count": int(total),
            "source_fingerprint": build_wrong_note_source_fingerprint(
                total=total,
                items=fingerprint_items,
            ),
            "next": next_offset,
            "prev": prev_offset,
            "results": items,
        }

        return Response(WrongNoteListResponseSerializer(payload).data)
