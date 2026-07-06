# PATH: apps/domains/assets/omr/views/omr_list_views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.assets.omr.services.meta_generator import MAX_MC_QUESTIONS, build_omr_meta
from apps.support.omr.view_dependencies import omr_template_assets_for_tenant


class ObjectiveOMRTemplateListView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        exam_id = request.query_params.get("exam_id")
        qs = omr_template_assets_for_tenant(
            tenant=request.tenant,
            exam_id=int(exam_id) if exam_id else None,
        )

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
        if question_count < 1 or question_count > MAX_MC_QUESTIONS:
            return Response({"detail": f"question_count: 1~{MAX_MC_QUESTIONS}"}, status=400)

        n_choices = int(request.query_params.get("n_choices", 5) or 5)
        essay_count = int(request.query_params.get("essay_count", 0) or 0)

        meta = build_omr_meta(
            question_count=question_count,
            n_choices=n_choices,
            essay_count=essay_count,
        )
        return Response(meta, status=200)
