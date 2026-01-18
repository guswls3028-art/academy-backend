# PATH: apps/domains/results/views/admin_clinic_targets_view.py
"""
역할
- Admin/Teacher용 클리닉 대상자 조회 API

Endpoint
- GET /results/admin/clinic-targets/

설계 계약 (중요)
- 대상자 선정 단일 진실: progress.ClinicLink(is_auto=True)
- enrollment_id 기준
- 계산/판정은 Service(ClinicTargetService)에 위임
- 응답 스키마는 AdminClinicTargetSerializer로 고정 (프론트 계약)

보류된 기능 (명시)
- pagination 필요 시 추후 DRF pagination 도입 가능
- 현재는 운영에서 "전체 대상자"가 소수라는 가정 하에 list로 반환
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.clinic_target_service import ClinicTargetService
from apps.domains.results.serializers.admin_clinic_target import AdminClinicTargetSerializer


class AdminClinicTargetsView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request):
        rows = ClinicTargetService.list_admin_targets()
        return Response(AdminClinicTargetSerializer(rows, many=True).data)
