from __future__ import annotations

from django.db.models import Q

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.template_with_usage import TemplateWithUsageSerializer


class TemplateWithUsageListView(APIView):
    """
    GET /api/v1/exams/templates/with-usage/

    목적:
    - "시험 불러오기" UI에서 템플릿 목록을 보여준다.
    - 각 템플릿이 실제로 적용중인 강의(derived regular 기준)를 강의 딱지로 표시할 수 있도록
      used_lectures 목록을 함께 반환한다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([])

        qs = (
            Exam.objects.filter(exam_type=Exam.ExamType.TEMPLATE)
            .filter(
                Q(derived_exams__sessions__lecture__tenant=tenant)
                | Q(sessions__lecture__tenant=tenant)
            )
            .distinct()
            .prefetch_related("derived_exams__sessions__lecture")
        )

        items = []
        for t in qs:
            lecture_map = {}
            for reg in getattr(t, "derived_exams", []).all():
                for s in getattr(reg, "sessions", []).all():
                    lec = getattr(s, "lecture", None)
                    if not lec:
                        continue
                    if getattr(lec, "tenant_id", None) != tenant.id:
                        continue
                    lecture_map[int(lec.id)] = str(getattr(lec, "title", "") or getattr(lec, "name", "") or f"lecture#{lec.id}")

            used_lectures = [
                {"lecture_id": lid, "lecture_title": lecture_map[lid]}
                for lid in sorted(lecture_map.keys())
            ]

            items.append(
                {
                    "id": int(t.id),
                    "title": str(t.title or ""),
                    "subject": str(t.subject or ""),
                    "used_lectures": used_lectures,
                }
            )

        return Response(TemplateWithUsageSerializer(items, many=True).data)

