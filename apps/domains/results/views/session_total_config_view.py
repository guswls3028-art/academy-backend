from drf_spectacular.utils import extend_schema, OpenApiTypes
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.serializers.assessment_feedback import AssessmentDetailErrorSerializer, ScoreTotalConfigResponseSerializer
from apps.support.results.score_total_dependencies import read_total_config, save_total_config


class TotalConfigSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=0)
    exam_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500)
    homework_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500)
    exam_order = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500, required=False)
    homework_order = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500, required=False)


class SessionTotalConfigView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        operation_id="results_admin_session_total_config_retrieve",
        responses={200: ScoreTotalConfigResponseSerializer, 403: AssessmentDetailErrorSerializer, 404: AssessmentDetailErrorSerializer},
        description="Staff-only tenant-scoped selected assessments and session-local column order. Newly linked assessments append to order; removed assessments disappear.",
    )
    def get(self, request, session_id):
        return Response(read_total_config(session_id=session_id, tenant=request.tenant))

    @extend_schema(
        operation_id="results_admin_session_total_config_update",
        request=TotalConfigSerializer,
        responses={200: ScoreTotalConfigResponseSerializer, 400: OpenApiTypes.OBJECT, 403: AssessmentDetailErrorSerializer, 404: AssessmentDetailErrorSerializer, 409: AssessmentDetailErrorSerializer},
        description="Atomically replace numeric-assessment selection and optional complete session-local order using expected_version. Completion-only homework is excluded from sums. Stale versions return 409; validation failure preserves the previous configuration.",
    )
    def put(self, request, session_id):
        serializer = TotalConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(save_total_config(session_id=session_id, tenant=request.tenant, payload=serializer.validated_data))
