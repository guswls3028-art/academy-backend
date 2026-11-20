from copy import deepcopy
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import TenantMembership
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.students.models import ClassGroupLecture, DawonTeacherAlertSettings
from apps.support.students.teacher_alert_evidence import DEFAULTS, signals_for_lectures

def changes(payload):
    if not isinstance(payload, dict) or set(payload) != {"rules"} or not isinstance(payload["rules"], dict):
        raise serializers.ValidationError("rules 객체를 입력하세요.")
    checked = {}
    for key, change in payload["rules"].items():
        if key not in DEFAULTS or not isinstance(change, dict) or not change or set(change) - {"enabled", "threshold"}:
            raise serializers.ValidationError("올바른 알림 규칙을 선택하세요.")
        checked[key] = {}
        if "enabled" in change:
            if type(change["enabled"]) is not bool:
                raise serializers.ValidationError({key: "enabled는 true/false여야 합니다."})
            checked[key]["enabled"] = change["enabled"]
        if "threshold" in change:
            maximum = 30 if key in ("absence_streak", "homework_missing") else 100
            checked[key]["threshold"] = serializers.IntegerField(min_value=1, max_value=maximum).run_validation(change["threshold"])
    return checked


class DawonTeacherAlertsView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def scope(self, request):
        tenant = request.tenant
        if not getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) or (tenant.pk, tenant.code) != (719, "dawon"):
            raise NotFound()
        roles = set(TenantMembership.objects.filter(tenant=tenant, user=request.user, is_active=True).values_list("role", flat=True))
        links = ClassGroupLecture.objects.filter(group__tenant=tenant, lecture__tenant=tenant, lecture__is_active=True)
        if not roles.intersection({"admin", "owner"}):
            if "teacher" not in roles:
                raise PermissionDenied()
            links = links.filter(reference_teacher=request.user)
        lectures = dict(links.order_by("lecture__title", "lecture_id").values_list("lecture_id", "lecture__title"))
        selected = request.query_params.get("lecture_id")
        if selected is None:
            return lectures, lectures
        pk = serializers.IntegerField(min_value=1).run_validation(selected)
        if pk not in lectures:
            raise NotFound()
        return lectures, {pk: lectures[pk]}

    def response(self, request, scope, stored):
        from apps.domains.students.services.dawon_followup import followups
        lectures, selected = scope
        rules = deepcopy(DEFAULTS)
        for key, saved in stored.items():
            if key in rules:
                rules[key].update(saved)
        return Response({"rules": rules, "lectures": [{"id": pk, "title": title} for pk, title in lectures.items()],
                         "alerts": signals_for_lectures(request.tenant, selected, timezone.localdate(timezone=ZoneInfo("Asia/Seoul")), rules),
                         "followups": followups(request.tenant, selected), "unavailable": []}, headers={"Cache-Control": "private, no-store"})

    def get(self, request):
        scope = self.scope(request)
        stored = DawonTeacherAlertSettings.objects.filter(tenant=request.tenant, teacher=request.user).values_list("rules", flat=True).first()
        return self.response(request, scope, stored or {})

    def patch(self, request):
        scope = self.scope(request)
        updates = changes(request.data)
        with transaction.atomic():
            stored, _ = DawonTeacherAlertSettings.objects.select_for_update().get_or_create(tenant=request.tenant, teacher=request.user)
            for key, change in updates.items():
                stored.rules[key] = {**stored.rules.get(key, {}), **change}
            stored.save(update_fields=["rules"])
        return self.response(request, scope, stored.rules)
