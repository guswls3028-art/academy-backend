# PATH: apps/domains/homework_results/views/homework_template_with_usage.py
"""
과제 템플릿 목록 (사용 중인 강의 포함) — 시험 templates/with-usage 와 동일 패턴
"""
from __future__ import annotations

from datetime import date as _date

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.homework_results.models import Homework


def template_visible_to_tenant(template: Homework, tenant) -> bool:
    """해당 테넌트가 이 템플릿을 사용할 수 있는지."""
    if template.homework_type != Homework.HomeworkType.TEMPLATE:
        return False
    # ✅ Tenant isolation: template must have at least one derived homework in this tenant
    return template.derived_homeworks.filter(
        session__lecture__tenant=tenant,
    ).exists()


class HomeworkTemplateWithUsageListView(APIView):
    """
    GET /api/v1/homeworks/templates/with-usage/

    과제 불러오기 UI용 템플릿 목록 + 사용 중인 강의(used_lectures).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([])

        qs = (
            Homework.objects.filter(homework_type=Homework.HomeworkType.TEMPLATE)
            .filter(derived_homeworks__session__lecture__tenant=tenant)
            .distinct()
            .prefetch_related("derived_homeworks__session__lecture")
        )

        items = []
        for t in qs:
            lecture_map = {}
            template_last = None
            for reg in t.derived_homeworks.select_related("session", "session__lecture").all():
                s = reg.session
                if not s or not getattr(s, "lecture", None):
                    continue
                lec = s.lecture
                if getattr(lec, "tenant_id", None) != tenant.id:
                    continue
                sid = lec.id
                lec_title = str(getattr(lec, "title", "") or getattr(lec, "name", "") or f"lecture#{sid}")
                lec_chip = (getattr(lec, "chip_label", "") or "")[:2] or (lec_title or "")[:2]
                lec_color = str(getattr(lec, "color", "") or "")
                session_date = getattr(s, "date", None) or (reg.updated_at.date() if reg.updated_at else None)
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

            used_lectures = sorted(
                lecture_map.values(),
                key=lambda x: (x.get("last_used_date") is not None, x.get("last_used_date") or _date.min),
                reverse=True,
            )
            for x in used_lectures:
                d = x.get("last_used_date")
                x["last_used_date"] = d.isoformat() if d else None

            items.append({
                "id": t.id,
                "title": str(t.title or ""),
                "last_used_date": template_last.isoformat() if template_last else None,
                "used_lectures": used_lectures,
            })

        items.sort(
            key=lambda x: (x.get("last_used_date") is not None, x.get("last_used_date") or "", x.get("title") or ""),
            reverse=True,
        )
        return Response(items)
