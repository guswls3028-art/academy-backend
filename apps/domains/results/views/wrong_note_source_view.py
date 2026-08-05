from __future__ import annotations

try:
    from drf_spectacular.utils import OpenApiParameter, extend_schema
except ModuleNotFoundError as exc:
    if exc.name != "drf_spectacular":
        raise

    class OpenApiParameter:  # type: ignore[no-redef]
        QUERY = "query"

        def __init__(self, *args, **kwargs):
            pass

    def extend_schema(*args, **kwargs):  # type: ignore[no-redef]
        """Keep runtime views importable when schema-only tooling is absent."""

        def decorator(view):
            return view

        return decorator
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.serializers.wrong_note_serializers import (
    WrongNoteListResponseSerializer,
    WrongNoteSelectedPreviewRequestSerializer,
    WrongNoteSelectedPreviewResponseSerializer,
    WrongNoteSourceCatalogResponseSerializer,
)
from apps.domains.results.services.selected_wrong_note_service import (
    WrongNoteSourceSelectionError,
    list_wrong_note_sources_for_student,
    list_wrong_notes_for_selection,
)
from apps.domains.results.services.wrong_note_service import (
    build_wrong_note_source_fingerprint,
)


def _student_id(raw_value) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValidationError({"student_id": "학생을 다시 선택해 주세요."})
    if value < 1:
        raise ValidationError({"student_id": "학생을 다시 선택해 주세요."})
    return value


class WrongNoteSourceCatalogView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="student_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
            )
        ],
        responses=WrongNoteSourceCatalogResponseSerializer,
    )
    def get(self, request):
        student_id = _student_id(request.query_params.get("student_id"))
        sources = list_wrong_note_sources_for_student(
            tenant_id=int(request.tenant.id),
            student_id=student_id,
        )
        return Response({"student_id": student_id, "sources": sources})


class WrongNoteSelectedPreviewView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        request=WrongNoteSelectedPreviewRequestSerializer,
        responses=WrongNoteSelectedPreviewResponseSerializer,
    )
    def post(self, request):
        student_id = _student_id(request.data.get("student_id"))
        try:
            total, items, normalized = list_wrong_notes_for_selection(
                tenant_id=int(request.tenant.id),
                student_id=student_id,
                source_selection=request.data.get("source_selection"),
            )
        except WrongNoteSourceSelectionError as exc:
            raise ValidationError({"source_selection": str(exc)}) from exc
        payload = {
            "count": total,
            "source_fingerprint": build_wrong_note_source_fingerprint(
                total=total,
                items=items,
            ),
            "next": None,
            "prev": None,
            "results": items,
        }
        return Response(
            {
                **WrongNoteListResponseSerializer(payload).data,
                "source_selection": normalized,
            }
        )
