from __future__ import annotations

from django.db.models import Q
from datetime import date as _date

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
            lecture_map: dict[int, dict] = {}
            template_last: _date | None = None

            for reg in getattr(t, "derived_exams", []).all():
                reg_fallback = getattr(reg, "created_at", None)
                reg_fallback_date = reg_fallback.date() if reg_fallback else None

                for s in getattr(reg, "sessions", []).all():
                    lec = getattr(s, "lecture", None)
                    if not lec:
                        continue
                    if getattr(lec, "tenant_id", None) != tenant.id:
                        continue

                    sid = int(getattr(lec, "id"))
                    lec_title = str(getattr(lec, "title", "") or getattr(lec, "name", "") or f"lecture#{sid}")
                    lec_chip = str(getattr(lec, "chip_label", "") or "").strip()
                    if not lec_chip:
                        lec_chip = (lec_title or "")[:2]
                    lec_color = str(getattr(lec, "color", "") or "").strip()

                    session_date = getattr(s, "date", None) or reg_fallback_date

                    cur = lecture_map.get(sid)
                    if not cur:
                        lecture_map[sid] = {
                            "lecture_id": sid,
                            "lecture_title": lec_title,
                            "chip_label": lec_chip,
                            "color": lec_color,
                            "last_used_date": session_date,
                        }
                    else:
                        prev = cur.get("last_used_date")
                        if session_date and (not prev or session_date > prev):
                            cur["last_used_date"] = session_date

                    if session_date and (template_last is None or session_date > template_last):
                        template_last = session_date

            # lecture 정렬: 최근 사용 강의 우선
            used_lectures = sorted(
                lecture_map.values(),
                key=lambda x: (x.get("last_used_date") is not None, x.get("last_used_date") or _date.min),
                reverse=True,
            )

            # date → str (serializer-friendly)
            for x in used_lectures:
                d = x.get("last_used_date")
                x["last_used_date"] = d.isoformat() if d else None

            items.append(
                {
                    "id": int(t.id),
                    "title": str(t.title or ""),
                    "subject": str(t.subject or ""),
                    "last_used_date": template_last.isoformat() if template_last else None,
                    "used_lectures": used_lectures,
                }
            )

        # 템플릿 정렬: 최근 사용 템플릿 우선
        items.sort(
            key=lambda x: (
                x.get("last_used_date") is not None,
                x.get("last_used_date") or "",
                x.get("title") or "",
            ),
            reverse=True,
        )

        return Response(TemplateWithUsageSerializer(items, many=True).data)

