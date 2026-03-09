# PATH: apps/domains/homework/views/homework_policy_viewset.py

from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.homework.models import HomeworkPolicy
from apps.domains.homework.serializers import (
    HomeworkPolicySerializer,
    HomeworkPolicyPatchSerializer,
)


class HomeworkPolicyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = HomeworkPolicySerializer

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        qs_base = HomeworkPolicy.objects.select_related("session").filter(tenant=tenant)

        # Detail action (retrieve, partial_update 등): pk로 조회 가능하도록 전체 queryset 반환
        if self.kwargs.get("pk"):
            return qs_base

        session_id = self.request.query_params.get("session")
        if not session_id:
            return qs_base.none()

        obj, _ = HomeworkPolicy.objects.get_or_create(
            tenant=tenant,
            session_id=int(session_id),
            defaults={
                "cutline_percent": 80,
                "cutline_mode": "PERCENT",
                "cutline_value": 80,
                "round_unit_percent": 5,
                "clinic_enabled": True,
                "clinic_on_fail": True,
            },
        )
        return qs_base.filter(id=obj.id)

    def partial_update(self, request, *args, **kwargs):
        obj = self.get_object()

        ser = HomeworkPolicyPatchSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()

        return Response(
            HomeworkPolicySerializer(obj).data,
            status=status.HTTP_200_OK,
        )
