# PATH: apps/domains/homework/views/homework_policy_viewset.py
# 역할: HomeworkPolicy API (프론트 계약: GET ?session= / PATCH {id}) + session당 1개 자동 생성

"""
HomeworkPolicy ViewSet

✅ 프론트 계약(LOCKED)
- GET   /homework/policies/?session={sessionId}
- PATCH /homework/policies/{id}/

✅ MVP 목표
- session 당 HomeworkPolicy 1개 단일 진실
- 없으면 자동 생성 (GET 시 보장)

⚠️ 원본 존중
- 권한 구조 유지
- 정책 생성 POST는 프론트에서 하지 않음(서버가 get_or_create)
"""

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
    queryset = HomeworkPolicy.objects.select_related("session").all()
    serializer_class = HomeworkPolicySerializer

    def get_queryset(self):
        """
        ✅ GET /homework/policies/?session=123

        - session 파라미터 없으면 empty
        - 있으면 session당 1개 보장(없으면 생성)
        """
        qs = super().get_queryset()

        session_id = self.request.query_params.get("session")
        if not session_id:
            return qs.none()

        try:
            sid = int(session_id)
        except Exception:
            return qs.none()

        obj, _ = HomeworkPolicy.objects.get_or_create(
            session_id=sid,
            defaults={
                "cutline_percent": 80,
                "round_unit_percent": 5,
                "clinic_enabled": True,
                "clinic_on_fail": True,
            },
        )

        return qs.filter(id=obj.id)

    def partial_update(self, request, *args, **kwargs):
        """
        ✅ PATCH /homework/policies/{id}/
        - 수정 가능 필드만 제한(PatchSerializer)
        """
        obj = self.get_object()

        ser = HomeworkPolicyPatchSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()

        return Response(HomeworkPolicySerializer(obj).data, status=status.HTTP_200_OK)
