from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.domains.exams.models import ExamAsset

class ObjectiveOMRTemplateListView(APIView):
    """
    기존에 생성되어 저장된 OMR 템플릿 목록을 조회합니다.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        exam_id = request.query_params.get("exam_id")
        qs = ExamAsset.objects.filter(asset_type="OMR_TEMPLATE")
        
        if exam_id:
            qs = qs.filter(exam_id=exam_id)
            
        data = [{
            "asset_id": a.id,
            "exam_id": a.exam_id,
            "question_count": a.meta.get("question_count"),
            "file_url": a.file.url if a.file else None,
            "created_at": a.created_at
        } for a in qs.order_by("-id")]
        
        return Response(data)