from __future__ import annotations

from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.tools.problem_studio.services import (
    build_problem_studio_package,
    parse_payload,
)


class ProblemStudioGenerateView(APIView):
    """POST /api/v1/tools/problem-studio/generate/

    업로드된 PDF/HWPX/DOCX와 현재 편집 중인 문항 텍스트를 학원 검수용
    문제/정답/해설 초안으로 변환한다. 기존 매치업/시험지 데이터는 변경하지 않는다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        try:
            payload = parse_payload(request.data.get("payload") if hasattr(request.data, "get") else request.data)
            if not payload and isinstance(request.data, dict):
                payload = dict(request.data)
            result = build_problem_studio_package(
                payload=payload,
                source_files=request.FILES.getlist("source_files"),
            )
            return Response(result)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
