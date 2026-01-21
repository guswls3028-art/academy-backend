# PATH: apps/domains/homework/views/homework_policy_viewset.py
"""
HomeworkPolicy ViewSet

✅ MVP 목표
- session 당 HomeworkPolicy 1개 단일 진실
- 없으면 자동 생성 (GET/PATCH 시 보장)
- cutline_percent PATCH 허용
- round_unit_percent PATCH 허용

⚠️ 원본 존중
- 기존 라우팅/권한 구조는 유지
- 변경은 "단일 정책 보장" + "PATCH 제한"만
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.homework.models import HomeworkPolicy
from apps.domains.homework.serializers import (
    HomeworkPolicySerializer,
    HomeworkPolicyPatchSerializer,
)


class HomeworkPolicyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _get_or_create_policy(self, session_id: int) -> HomeworkPolicy:
        # ✅ session당 1개 보장 (없으면 생성)
        obj, _ = HomeworkPolicy.objects.get_or_create(
            session_id=session_id,
            defaults={
                "cutline_percent": 80,
                "round_unit_percent": 5,
            },
        )
        return obj

    @action(detail=False, methods=["get"], url_path="session")
    def session_policy(self, request):
        """
        GET /homework/policies/session/?session_id=123

        ✅ 없으면 자동 생성
        """
        session_id = request.query_params.get("session_id")
        if not session_id:
            return Response({"detail": "session_id required"}, status=400)

        policy = self._get_or_create_policy(int(session_id))
        return Response(HomeworkPolicySerializer(policy).data)

    @action(detail=False, methods=["patch"], url_path="session")
    def patch_session_policy(self, request):
        """
        PATCH /homework/policies/session/?session_id=123
        body:
        {
            "cutline_percent": 80,
            "round_unit_percent": 5
        }

        ✅ 없으면 자동 생성 후 patch 적용
        """
        session_id = request.query_params.get("session_id")
        if not session_id:
            return Response({"detail": "session_id required"}, status=400)

        policy = self._get_or_create_policy(int(session_id))

        ser = HomeworkPolicyPatchSerializer(policy, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()

        return Response(HomeworkPolicySerializer(policy).data, status=status.HTTP_200_OK)
