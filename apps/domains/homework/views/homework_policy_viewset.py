# PATH: apps/domains/homework/views/homework_policy_viewset.py

from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.domains.homework.models import HomeworkPolicy
from apps.domains.homework.serializers import (
    HomeworkPolicySerializer,
    HomeworkPolicyPatchSerializer,
)
from apps.domains.lectures.models import Session
from apps.domains.homework_results.models import HomeworkScore


def _recalc_scores_for_policy_change(*, policy: HomeworkPolicy) -> int:
    """
    HomeworkPolicy 변경 시, 이미 생성된 HomeworkScore 스냅샷(passed/clinic_required)을 재계산한다.

    NOTE:
    - 점수 입력 시점에만 passed 계산하면, 정책(커트라인) 변경이 결과 화면에 반영되지 않는 문제가 발생한다.
    - 여기서는 score/max_score와 policy만으로 재계산 가능한 필드만 갱신한다.
    - meta.status="NOT_SUBMITTED" 등 Progress/ClinicLink 연동은 다른 파이프라인(SSOT)이 담당한다.
    """
    session = policy.session
    tenant = policy.tenant

    qs = HomeworkScore.objects.filter(
        session=session,
        session__lecture__tenant=tenant,
    ).only(
        "id",
        "score",
        "max_score",
        "passed",
        "clinic_required",
        "updated_at",
    )

    mode = str(getattr(policy, "cutline_mode", "PERCENT") or "PERCENT")
    cutline_value = int(getattr(policy, "cutline_value", 0) or 0)
    round_unit = int(getattr(policy, "round_unit_percent", 5) or 5)
    clinic_enabled = bool(getattr(policy, "clinic_enabled", True))
    clinic_on_fail = bool(getattr(policy, "clinic_on_fail", True))

    if round_unit <= 0:
        round_unit = 1

    now = timezone.now()
    changed: list[HomeworkScore] = []

    for hs in qs.iterator(chunk_size=500):
        score = hs.score
        max_score = hs.max_score

        passed = False
        clinic_required = False

        if mode == "COUNT":
            # COUNT: score는 정답 문항 수/점수로 해석
            if score is not None:
                try:
                    passed = bool(float(score) >= float(cutline_value))
                except Exception:
                    passed = False
            clinic_required = bool(clinic_enabled and clinic_on_fail and (not passed))
        else:
            # PERCENT: percent 계산 후 round_unit 단위로 반올림
            percent: float | None
            if score is None:
                percent = None
            elif max_score is None:
                percent = float(score)
            else:
                if float(max_score) == 0.0:
                    percent = None
                else:
                    percent = (float(score) / float(max_score)) * 100.0

            if percent is None:
                passed = False
                clinic_required = False
            else:
                rounded = int(round(float(percent) / float(round_unit)) * float(round_unit))
                threshold = int(cutline_value or 0)
                if threshold <= 0:
                    threshold = 80
                passed = bool(rounded >= threshold)
                clinic_required = bool(clinic_enabled and clinic_on_fail and (not passed))

        if hs.passed != bool(passed) or hs.clinic_required != bool(clinic_required):
            hs.passed = bool(passed)
            hs.clinic_required = bool(clinic_required)
            hs.updated_at = now
            changed.append(hs)

    if changed:
        HomeworkScore.objects.bulk_update(
            changed,
            fields=["passed", "clinic_required", "updated_at"],
            batch_size=500,
        )

    return len(changed)


class HomeworkPolicyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
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

        # tenant 미설정 시 get_or_create 시 500 방지
        if not tenant:
            return qs_base.none()

        try:
            sid = int(session_id)
        except (TypeError, ValueError):
            return qs_base.none()

        # session 존재 및 해당 tenant 소유 여부 검증 (500/잘못된 정책 생성 방지)
        if not Session.objects.filter(id=sid, lecture__tenant=tenant).exists():
            return qs_base.none()

        try:
            with transaction.atomic():
                obj, _ = HomeworkPolicy.objects.get_or_create(
                    tenant=tenant,
                    session_id=sid,
                    defaults={
                        "cutline_percent": 80,
                        "cutline_mode": "PERCENT",
                        "cutline_value": 80,
                        "round_unit_percent": 5,
                        "clinic_enabled": True,
                        "clinic_on_fail": True,
                    },
                )
        except IntegrityError:
            # 레이스 컨디션 등으로 create가 실패하면, 이미 만들어진 row를 재조회
            obj = HomeworkPolicy.objects.filter(tenant=tenant, session_id=sid).first()
            if not obj:
                return qs_base.none()
        return qs_base.filter(id=obj.id)

    def partial_update(self, request, *args, **kwargs):
        obj = self.get_object()

        ser = HomeworkPolicyPatchSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()

        # ✅ 정책 변경이 결과(passed)에도 반영되도록 스냅샷 재계산
        # (프론트: policy 저장 후 session-scores invalidate 필요)
        _recalc_scores_for_policy_change(policy=obj)

        return Response(
            HomeworkPolicySerializer(obj).data,
            status=status.HTTP_200_OK,
        )
