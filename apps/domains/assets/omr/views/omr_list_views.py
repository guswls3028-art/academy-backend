# PATH: apps/domains/assets/omr/views/omr_list_views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.assets.omr.dto.omr_document import MAX_ESSAY_QUESTIONS
from apps.domains.assets.omr.services.meta_generator import MAX_MC_QUESTIONS, build_omr_meta
from apps.support.omr.view_dependencies import omr_template_assets_for_tenant


class ObjectiveOMRTemplateQuerySerializer(serializers.Serializer):
    exam_id = serializers.IntegerField(min_value=1, required=False)


class ObjectiveOMRMetaQuerySerializer(serializers.Serializer):
    question_count = serializers.IntegerField(
        min_value=1,
        max_value=MAX_MC_QUESTIONS,
    )
    n_choices = serializers.ChoiceField(choices=[5], default=5)
    essay_count = serializers.IntegerField(
        min_value=0,
        max_value=MAX_ESSAY_QUESTIONS,
        default=0,
    )


class ObjectiveOMRTemplateListView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        query = ObjectiveOMRTemplateQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        qs = omr_template_assets_for_tenant(
            tenant=request.tenant,
            exam_id=query.validated_data.get("exam_id"),
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
        query = ObjectiveOMRMetaQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        meta = build_omr_meta(
            question_count=query.validated_data["question_count"],
            n_choices=query.validated_data["n_choices"],
            essay_count=0,
        )
        return Response(meta, status=200)
