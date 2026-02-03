# apps/domains/assets/omr/views/omr_list_views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.domains.exams.models import ExamAsset

class ObjectiveOMRTemplateListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 템플릿 타입의 에셋만 필터링
        qs = ExamAsset.objects.filter(asset_type="OMR_TEMPLATE")
        
        exam_id = request.query_params.get("exam_id")
        if exam_id:
            qs = qs.filter(exam_id=exam_id)

        items = []
        for asset in qs.order_by("-id"):
            meta = asset.meta or {}
            items.append({
                "asset_id": asset.id,
                "exam_id": asset.exam_id,
                "question_count": meta.get("question_count", 45),
                "has_logo": meta.get("has_logo", False),
                "file_url": asset.file.url if asset.file else None,
                "version": meta.get("version", "v2"),
                "created_at": asset.created_at.strftime("%Y-%m-%d %H:%M")
            })
        
        return Response(items, status=200)