# PATH: apps/domains/homework_results/views/homework_save_as_template_view.py
"""
POST /api/v1/homeworks/<homework_id>/save-as-template/

regular 과제가 template_homework 없을 때, 현재 설정으로 템플릿을 생성해 연결.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.homework_results.models import Homework
from apps.domains.homework_results.serializers.homework import HomeworkSerializer
from apps.domains.results.permissions import IsTeacherOrAdmin


class HomeworkSaveAsTemplateView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]

    def post(self, request, homework_id):
        hw = self._get_regular_homework(request, int(homework_id))
        if hw.template_homework_id is not None:
            raise ValidationError({"detail": "이미 템플릿이 연결되어 있습니다."})

        template = Homework.objects.create(
            homework_type=Homework.HomeworkType.TEMPLATE,
            session=None,
            template_homework=None,
            title=hw.title,
            status=Homework.Status.DRAFT,
            meta=hw.meta,
        )
        hw.template_homework = template
        hw.save(update_fields=["template_homework_id"])

        return Response(HomeworkSerializer(hw).data)

    def _get_regular_homework(self, request, homework_id):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant required.")
        try:
            hw = Homework.objects.filter(
                session__lecture__tenant=tenant,
                homework_type=Homework.HomeworkType.REGULAR,
            ).get(id=homework_id)
        except Homework.DoesNotExist:
            raise NotFound("과제를 찾을 수 없습니다.")
        return hw
