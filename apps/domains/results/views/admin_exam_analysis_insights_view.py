from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.common.query_params import parse_query_int
from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.exam_analysis_metrics import load_exam_analysis
from apps.support.results.admin_exam_dependencies import get_regular_active_exam_for_tenant
from apps.support.results.exam_policy_dependencies import exam_has_linked_lecture


class AdminExamAnalysisInsightsView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        tenant = request.tenant
        exam = get_regular_active_exam_for_tenant(exam_id=exam_id, tenant=tenant)
        lecture_id = parse_query_int(request.query_params, "lecture_id", min_value=1)
        if lecture_id is not None and not exam_has_linked_lecture(
            exam_id=exam_id, lecture_id=lecture_id, tenant=tenant,
        ):
            raise ValidationError({"lecture_id": "이 시험에 연결되지 않은 강의입니다."})
        analysis = load_exam_analysis(exam=exam, tenant=tenant, lecture_id=lecture_id)
        return Response(analysis.insights, headers={"Cache-Control": "private, no-store"})
