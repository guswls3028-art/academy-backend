from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.submissions.homework_acceptance import set_homework_file_acceptance


class HomeworkFileAcceptanceUpdateSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField(min_value=1)
    accepted = serializers.BooleanField()
    expected_updated_at = serializers.DateTimeField(allow_null=True)
    expected_media_set_fingerprint = serializers.RegexField(r"\A[a-f0-9]{64}\Z")


class HomeworkFileAcceptanceSerializer(serializers.Serializer):
    teacher_accepted = serializers.BooleanField()
    teacher_accepted_updated_at = serializers.DateTimeField(allow_null=True)


class HomeworkFileAcceptanceView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(request=HomeworkFileAcceptanceUpdateSerializer, responses=HomeworkFileAcceptanceSerializer)
    def patch(self, request, homework_id: int):
        update = HomeworkFileAcceptanceUpdateSerializer(data=request.data)
        update.is_valid(raise_exception=True)
        return Response(set_homework_file_acceptance(
            tenant=request.tenant, homework_id=homework_id, **update.validated_data,
        ))
