# PATH: apps/domains/assets/omr/views/omr_list_views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import ExamAsset
from apps.domains.assets.omr.services.meta_generator import (
    build_objective_template_meta,
    ALLOWED_QUESTION_COUNTS,
)


class ObjectiveOMRTemplateListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ExamAsset.objects.filter(asset_type="OMR_TEMPLATE")

        exam_id = request.query_params.get("exam_id")
        if exam_id:
            qs = qs.filter(exam_id=exam_id)

        items = []
        for asset in qs.order_by("-id"):
            meta = asset.meta or {}
            items.append(
                {
                    "asset_id": asset.id,
                    "exam_id": asset.exam_id,
                    "question_count": meta.get("question_count"),
                    "has_logo": meta.get("has_logo", False),
                    "file_url": asset.file.url if asset.file else None,
                    "version": meta.get("version", "objective_ssot_v1"),
                    "created_at": asset.created_at.strftime("%Y-%m-%d %H:%M"),
                }
            )

        return Response(items, status=200)


class ObjectiveOMRMetaView(APIView):
    """
    GET /api/v1/assets/omr/objective/meta/?question_count=10|20|30
    OmrObjectiveMetaV1 형식 반환 (mm 단위 roi).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qc_raw = request.query_params.get("question_count")
        if qc_raw is None:
            return Response({"question_count": "required"}, status=400)
        try:
            question_count = int(str(qc_raw).strip())
        except (TypeError, ValueError):
            return Response({"question_count": "must be one of 10, 20, 30"}, status=400)
        if question_count not in ALLOWED_QUESTION_COUNTS:
            return Response({"question_count": "must be one of 10, 20, 30"}, status=400)
        try:
            meta = build_objective_template_meta(question_count=question_count)
        except ValueError:
            return Response({"question_count": "must be one of 10, 20, 30"}, status=400)
        return Response(meta, status=200)
