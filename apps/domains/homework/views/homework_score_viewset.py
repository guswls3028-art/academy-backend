# PATH: apps/domains/homework/views/homework_score_viewset.py
# 역할: HomeworkScore 조회/수정 + quick_patch(upsert)로 점수입력(% or raw/max) 지원

"""
HomeworkScore API (Admin / Teacher)

Endpoint:
- GET    /homework/scores/?enrollment_id=&session=&lecture=&is_locked=
- PATCH  /homework/scores/{id}/
- PATCH  /homework/scores/quick/

설계 계약 (LOCKED):
- Homework 합불(passed)은 PATCH 시점에만 계산
- SessionScores API는 passed / clinic_required 값을 그대로 신뢰

✅ IMPORTANT (리팩토링)
- HomeworkScore 스냅샷의 단일 진실은 homework_results 도메인이다.
- 하지만 /homework/scores/* 라우팅은 프론트 호환을 위해 유지한다.

Quick Patch (MVP):
- homework_id + enrollment_id 기반 upsert
- score 입력 방식 2개 지원:
  - percent 직접 입력(score=85, max_score=None)
  - raw/max 입력(score=18, max_score=20)
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db import IntegrityError
from django.db.models import QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as drf_status
from rest_framework.decorators import action

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

# ✅ 단일 진실
from apps.domains.homework_results.models import HomeworkScore
from apps.domains.homework_results.models import Homework

from apps.domains.homework_results.serializers import (
    HomeworkScoreSerializer,
)
from apps.domains.homework.serializers import (
    HomeworkQuickPatchSerializer,
)
from apps.domains.homework.filters import HomeworkScoreFilter

from apps.core.permissions import TenantResolvedAndStaff

# 🔐 enrollment tenant guard
from apps.domains.results.guards.enrollment_tenant_guard import validate_enrollment_belongs_to_tenant

# submissions 기준 보정 (기존 구조 유지)
from apps.domains.submissions.models import Submission

# progress pipeline 단일 진실 (기존 구조 유지)
from apps.domains.progress.dispatcher import dispatch_progress_pipeline

# homework policy 계산 유틸 (HomeworkPolicy 단일 진실)
from apps.domains.homework.utils.homework_policy import (
    calc_homework_passed_and_clinic,
)

# ClinicLink 관리 (과제 합불 → 클리닉 대상 생성/해소)
from apps.domains.progress.models import ClinicLink
from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService


# =====================================================
# helpers
# =====================================================
logger = logging.getLogger(__name__)


def _safe_user_id(request) -> int | None:
    return getattr(getattr(request, "user", None), "id", None)


def _sync_homework_clinic_link(
    *,
    enrollment_id: int,
    session,
    homework_id: int,
    passed: bool,
    score: float | None,
    max_score: float | None,
) -> None:
    """
    과제 합불 결과에 따라 ClinicLink를 생성하거나 해소한다.
    - 불합격(passed=False): 미해소 ClinicLink가 없으면 생성
    - 합격(passed=True): 미해소 ClinicLink가 있으면 해소
    """
    if passed:
        # 합격 → 미해소 ClinicLink 해소
        ClinicResolutionService.resolve_by_homework_pass(
            enrollment_id=enrollment_id,
            session_id=session.id,
            homework_id=homework_id,
            score=score,
            max_score=max_score,
        )
    else:
        # 불합격/미제출 → ClinicLink 생성 (idempotent)
        existing_unresolved = ClinicLink.objects.filter(
            enrollment_id=enrollment_id,
            session=session,
            source_type="homework",
            source_id=homework_id,
            resolved_at__isnull=True,
        ).exists()
        if not existing_unresolved:
            from django.db.models import Max
            max_cycle = ClinicLink.objects.filter(
                enrollment_id=enrollment_id,
                session=session,
                source_type="homework",
                source_id=homework_id,
            ).aggregate(Max("cycle_no"))["cycle_no__max"] or 0

            from django.db import IntegrityError as DjangoIntegrityError
            # tenant_id 조회
            from apps.domains.enrollment.models import Enrollment as _Enrollment
            _tenant_id = _Enrollment.objects.filter(id=enrollment_id).values_list("tenant_id", flat=True).first()
            try:
                ClinicLink.objects.create(
                    enrollment_id=enrollment_id,
                    session=session,
                    source_type="homework",
                    source_id=homework_id,
                    reason=ClinicLink.Reason.AUTO_FAILED,
                    is_auto=True,
                    approved=False,
                    cycle_no=max(max_cycle + 1, 1),
                    tenant_id=_tenant_id,
                    meta={
                        "kind": "HOMEWORK_FAILED",
                        "homework_id": homework_id,
                        "score": score,
                        "max_score": max_score,
                    },
                )
            except DjangoIntegrityError:
                # race condition: 동시 요청으로 이미 생성됨 — 정상 케이스
                pass


def _locked_response(obj: HomeworkScore) -> Response:
    return Response(
        {
            "detail": "score block is locked",
            "code": "LOCKED",
            "lock_reason": getattr(obj, "lock_reason", None),
        },
        status=drf_status.HTTP_409_CONFLICT,
    )


def _apply_score_and_policy(
    *,
    obj: HomeworkScore,
    score: float | None,
    max_score: float | None,
    request,
    save_fields: list[str],
) -> HomeworkScore:
    """
    HomeworkScore에 점수 반영 + HomeworkPolicy 계산
    (동작 변경 없음 / 중복 제거용)
    """
    obj.score = score
    obj.max_score = max_score

    passed, clinic_required, _ = calc_homework_passed_and_clinic(
        session=obj.session,
        score=score,
        max_score=max_score,
    )

    obj.passed = bool(passed)
    obj.clinic_required = bool(clinic_required)
    obj.updated_by_user_id = _safe_user_id(request)

    obj.save(update_fields=save_fields + ["updated_at"])
    return obj


def _maybe_fix_submission(
    submission: Submission,
    *,
    score_obj: HomeworkScore,
    teacher_approved: bool | None,
) -> None:
    """
    Submission 구조는 프로젝트마다 다를 수 있으므로
    "필드가 존재할 때만" 방어적으로 보정한다.
    """
    if hasattr(submission, "homework_submitted"):
        submission.homework_submitted = True

    if hasattr(submission, "homework_teacher_approved") and teacher_approved is not None:
        submission.homework_teacher_approved = bool(teacher_approved)

    if hasattr(submission, "meta"):
        meta = submission.meta if isinstance(submission.meta, dict) else {}
        meta = {**meta}

        homework_meta = meta.get("homework")
        if not isinstance(homework_meta, dict):
            homework_meta = {}

        homework_meta.update(
            {
                "homework_score_id": score_obj.id,
                "homework_id": score_obj.homework_id,
                "score": score_obj.score,
                "max_score": score_obj.max_score,
                "passed": score_obj.passed,
                "clinic_required": score_obj.clinic_required,
                "teacher_approved": getattr(
                    submission, "homework_teacher_approved", None
                ),
            }
        )

        meta["homework"] = homework_meta
        submission.meta = meta

    update_fields = ["updated_at"]
    for f in ["homework_submitted", "homework_teacher_approved", "meta"]:
        if hasattr(submission, f):
            update_fields.append(f)

    submission.save(update_fields=list(dict.fromkeys(update_fields)))


# =====================================================
# ViewSet
# =====================================================
class HomeworkScoreViewSet(ModelViewSet):
    """
    HomeworkScore 관리 ViewSet
    """

    serializer_class = HomeworkScoreSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_queryset(self) -> QuerySet[HomeworkScore]:
        return (
            HomeworkScore.objects
            .select_related("session", "session__lecture", "homework")
            .filter(
                session__lecture__tenant=self.request.tenant,
                attempt_index=1,  # 성적 탭은 1차(성적 산출 대상)만 조회
            )
        )

    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]
    filterset_class = HomeworkScoreFilter

    search_fields = [
        "enrollment_id",
        "session__title",
        "session__lecture__title",
        "homework__title",
    ]

    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "is_locked",
        "score",
        "passed",
    ]
    ordering = ["-updated_at", "-id"]

    # =================================================
    # PATCH /homework/scores/{id}/
    # =================================================
    def partial_update(self, request, *args, **kwargs):
        obj: HomeworkScore = self.get_object()

        # 🔐 enrollment tenant guard: obj의 enrollment이 현재 tenant에 속하는지 검증
        validate_enrollment_belongs_to_tenant(obj.enrollment_id, request.tenant)

        if getattr(obj, "is_locked", False):
            return _locked_response(obj)

        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        vd = serializer.validated_data
        next_score = vd.get("score", obj.score)
        next_max = vd.get("max_score", obj.max_score)
        teacher_approved = vd.get("teacher_approved")

        progress_info = {"dispatched": False, "reason": None}

        with transaction.atomic():
            serializer.save(
                passed=obj.passed,
                clinic_required=obj.clinic_required,
                updated_by_user_id=_safe_user_id(request),
            )

            score_obj: HomeworkScore = serializer.instance

            score_obj = _apply_score_and_policy(
                obj=score_obj,
                score=next_score,
                max_score=next_max,
                request=request,
                save_fields=[
                    "score",
                    "max_score",
                    "passed",
                    "clinic_required",
                    "updated_by_user_id",
                ],
            )

            submission = (
                Submission.objects.filter(
                    enrollment_id=score_obj.enrollment_id,
                    target_type="homework",
                    target_id=score_obj.homework_id,
                )
                .order_by("-id")
                .first()
            )

            if submission:
                _maybe_fix_submission(
                    submission,
                    score_obj=score_obj,
                    teacher_approved=teacher_approved,
                )

                sub_id = int(submission.id)

                def _dispatch():
                    try:
                        dispatch_progress_pipeline(submission_id=sub_id)
                    except Exception:
                        pass

                transaction.on_commit(_dispatch)
                progress_info = {"dispatched": True, "reason": None}
            else:
                progress_info = {"dispatched": False, "reason": "NO_SUBMISSION"}

            # ✅ ClinicLink 동기화: Submission 없어도 과제 합불에 따라 생성/해소
            # 중첩 savepoint로 감싸서 IntegrityError가 외부 트랜잭션을 깨지 않도록 보호
            try:
                with transaction.atomic():
                    _sync_homework_clinic_link(
                        enrollment_id=score_obj.enrollment_id,
                        session=score_obj.session,
                        homework_id=score_obj.homework_id,
                        passed=bool(score_obj.passed),
                        score=score_obj.score,
                        max_score=score_obj.max_score,
                    )
            except Exception:
                logger.exception(
                    "partial_update: clinic link sync failed (hw=%s, enrollment=%s)",
                    score_obj.homework_id, score_obj.enrollment_id,
                )

        data = self.get_serializer(score_obj).data
        data["progress"] = progress_info

        return Response(data, status=drf_status.HTTP_200_OK)

    # =================================================
    # PATCH /homework/scores/quick/
    # =================================================
    @action(detail=False, methods=["patch"], url_path="quick")
    def quick_patch(self, request):
        """
        Quick input (MVP)

        - % 입력: score=85, max_score 생략 (percent 직접 입력)
        - raw/max: score=32, max_score=64
        - 미제출: meta_status="NOT_SUBMITTED", score=null
        """
        try:
            serializer = HomeworkQuickPatchSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            homework_id = serializer.validated_data["homework_id"]
            enrollment_id = serializer.validated_data["enrollment_id"]

            # 🔐 enrollment tenant guard: enrollment이 현재 tenant에 속하는지 검증
            validate_enrollment_belongs_to_tenant(enrollment_id, request.tenant)

            score = serializer.validated_data.get("score")
            max_score = serializer.validated_data.get("max_score")
            meta_status = serializer.validated_data.get("meta_status")

            # ✅ 단일 진실: homework → session (DoesNotExist → 404)
            # 🔐 tenant isolation: homework must belong to request tenant
            homework = get_object_or_404(
                Homework.objects.select_related("session", "session__lecture", "session__lecture__tenant").filter(
                    session__lecture__tenant=self.request.tenant,
                ),
                id=homework_id,
            )
            session = homework.session

            with transaction.atomic():
                # ✅ attempt_index=1: 성적 페이지는 1차(성적 산출 대상)만 조회/수정
                obj = (
                    HomeworkScore.objects.select_for_update()
                    .filter(
                        homework_id=homework_id,
                        enrollment_id=enrollment_id,
                        attempt_index=1,
                    )
                    .select_related("session", "homework")
                    .first()
                )

                if obj and obj.is_locked:
                    return _locked_response(obj)

                if not obj:
                    try:
                        obj = HomeworkScore.objects.create(
                            homework=homework,
                            session=session,
                            enrollment_id=enrollment_id,
                            attempt_index=1,
                            score=None,
                            max_score=None,
                            updated_by_user_id=_safe_user_id(request),
                        )
                    except IntegrityError:
                        obj = (
                            HomeworkScore.objects.filter(
                                homework_id=homework_id,
                                enrollment_id=enrollment_id,
                                attempt_index=1,
                            )
                            .select_related("session", "homework")
                            .first()
                        )
                        if not obj:
                            raise
                        if obj.is_locked:
                            return _locked_response(obj)

                if meta_status == HomeworkScore.MetaStatus.NOT_SUBMITTED:
                    obj.meta = {"status": HomeworkScore.MetaStatus.NOT_SUBMITTED}
                    obj.score = None
                    obj.max_score = None
                    obj.updated_by_user_id = _safe_user_id(request)
                    passed, clinic_required, _ = calc_homework_passed_and_clinic(
                        session=obj.session,
                        score=None,
                        max_score=None,
                    )
                    obj.passed = bool(passed)
                    obj.clinic_required = bool(clinic_required)
                    obj.save(update_fields=["meta", "score", "max_score", "passed", "clinic_required", "updated_by_user_id", "updated_at"])
                else:
                    obj = _apply_score_and_policy(
                        obj=obj,
                        score=score,
                        max_score=max_score,
                        request=request,
                        save_fields=[
                            "score",
                            "max_score",
                            "passed",
                            "clinic_required",
                            "updated_by_user_id",
                        ],
                    )
                    if obj.meta and isinstance(obj.meta, dict) and obj.meta.get("status") == HomeworkScore.MetaStatus.NOT_SUBMITTED:
                        obj.meta = {k: v for k, v in obj.meta.items() if k != "status"}
                        if not obj.meta:
                            obj.meta = None
                        obj.save(update_fields=["meta", "updated_at"])

                # ✅ ClinicLink 동기화: 과제 합불 결과에 따라 생성/해소
                # 중첩 savepoint로 감싸서 IntegrityError가 외부 트랜잭션을 깨지 않도록 보호
                try:
                    with transaction.atomic():
                        _sync_homework_clinic_link(
                            enrollment_id=enrollment_id,
                            session=session,
                            homework_id=homework_id,
                            passed=bool(obj.passed),
                            score=obj.score,
                            max_score=obj.max_score,
                        )
                except Exception:
                    logger.exception(
                        "quick_patch: clinic link sync failed (hw=%s, enrollment=%s)",
                        homework_id, enrollment_id,
                    )

            return Response(
                HomeworkScoreSerializer(obj).data,
                status=drf_status.HTTP_200_OK,
            )
        except Http404:
            raise
        except Exception as e:
            logger.exception("quick_patch failed: %s", e)
            # DEBUG일 때만 응답 본문에 오류 노출 (Network 탭에서 확인 가능)
            if getattr(settings, "DEBUG", False):
                return Response(
                    {
                        "detail": str(e),
                        "type": type(e).__name__,
                        "hint": "백엔드 콘솔에 전체 traceback이 출력됩니다. DEBUG=True로 실행 중인지 확인하세요.",
                    },
                    status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            raise
