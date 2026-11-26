from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.common.query_params import parse_query_int
from apps.core.permissions import TenantResolvedAndStaff
from apps.support.results.dashboard_dependencies import pending_homework_submission_page


class HomeworkReviewQueueRowSerializer(serializers.Serializer):
    homework_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()
    homework_title = serializers.CharField()
    lecture_title = serializers.CharField()
    session_title = serializers.CharField()
    submitted_at = serializers.DateTimeField()
    file_count = serializers.IntegerField()


class HomeworkReviewQueuePageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    pages = serializers.IntegerField()
    results = HomeworkReviewQueueRowSerializer(many=True)


class HomeworkReviewQueueView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(parameters=[OpenApiParameter("page", int), OpenApiParameter("page_size", int)],
                   responses=HomeworkReviewQueuePageSerializer)
    def get(self, request):
        page = parse_query_int(request.query_params, "page", min_value=1, max_value=1_000_000, default=1)
        size = parse_query_int(request.query_params, "page_size", min_value=1, max_value=100, default=30)
        return Response(pending_homework_submission_page(tenant=request.tenant, page_number=page, page_size=size))
