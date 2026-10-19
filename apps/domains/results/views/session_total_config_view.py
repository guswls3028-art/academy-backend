from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.results.score_total_dependencies import read_total_config, save_total_config


class TotalConfigSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=0)
    exam_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500)
    homework_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500)
    exam_order = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500, required=False)
    homework_order = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500, required=False)


class SessionTotalConfigView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, session_id):
        return Response(read_total_config(session_id=session_id, tenant=request.tenant))

    def put(self, request, session_id):
        serializer = TotalConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(save_total_config(session_id=session_id, tenant=request.tenant, payload=serializer.validated_data))
