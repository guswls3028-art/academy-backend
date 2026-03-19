# PATH: apps/domains/assets/omr/views/omr_list_views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.exams.models import ExamAsset
from apps.domains.assets.omr.services.meta_generator import build_omr_meta


class ObjectiveOMRTemplateListView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        qs = ExamAsset.objects.filter(
            asset_type="OMR_TEMPLATE",
            exam__sessions__lecture__tenant=request.tenant,
        ).distinct()

        exam_id = request.query_params.get("exam_id")
        if exam_id:
            qs = qs.filter(exam_id=exam_id)

        items = []
        for asset in qs.order_by("-id"):
            meta = asset.meta or {}
            items.append({
                "asset_id": asset.id,
                "exam_id": asset.exam_id,
                "question_count": meta.get("question_count") or meta.get("mc_count"),
                "version": meta.get("version", "v7"),
                "created_at": asset.created_at.strftime("%Y-%m-%d %H:%M"),
            })

        return Response(items, status=200)


class ObjectiveOMRMetaView(APIView):
    """
    GET /api/v1/assets/omr/objective/meta/?question_count=N&n_choices=5&essay_count=0
    OMR v7 메타 반환 (mm 단위 좌표).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qc_raw = request.query_params.get("question_count")
        if qc_raw is None:
            return Response({"detail": "question_count required"}, status=400)
        try:
            question_count = int(str(qc_raw).strip())
        except (TypeError, ValueError):
            return Response({"detail": "question_count must be integer"}, status=400)
        if question_count < 1 or question_count > 45:
            return Response({"detail": "question_count: 1~45"}, status=400)

        n_choices = int(request.query_params.get("n_choices", 5) or 5)
        essay_count = int(request.query_params.get("essay_count", 0) or 0)

        meta = build_omr_meta(
            question_count=question_count,
            n_choices=n_choices,
            essay_count=essay_count,
        )
        return Response(meta, status=200)
