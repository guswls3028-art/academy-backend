# PATH: apps/domains/assets/omr/views/omr_list_views.py
from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.exams.models import ExamAsset


class ObjectiveOMRTemplateListView(APIView):
    """
    GET /api/v1/assets/omr/objective/templates/

    query params (optional):
      - exam_id: int

    response:
      [
        {
          "asset_id": number,
          "exam_id": number,
          "question_count": 10 | 20 | 30,
          "version": "objective_v1",
          "has_logo": bool,
          "file_url": string
        }
      ]

    책임:
    - assets 범위 내 "저장된 OMR 양식" 조회만 담당
    - 시험/채점/결과 로직 관여 ❌
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ExamAsset.objects.filter(asset_type="OMR_TEMPLATE").order_by("-id")

        exam_id_raw = request.query_params.get("exam_id")
        if exam_id_raw:
            try:
                exam_id = int(str(exam_id_raw).strip())
                qs = qs.filter(exam_id=exam_id)
            except Exception:
                return Response({"exam_id": "must be an integer"}, status=400)

        items = []
        for asset in qs:
            meta = asset.meta or {}
            items.append(
                {
                    "asset_id": asset.id,
                    "exam_id": asset.exam_id,
                    "question_count": meta.get("question_count"),
                    "version": meta.get("version"),
                    "has_logo": bool(meta.get("has_logo")),
                    "file_url": asset.file.url if asset.file else None,
                }
            )

        return Response(items, status=200)
