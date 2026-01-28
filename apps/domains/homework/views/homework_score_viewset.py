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
- session_id + enrollment_id + homework_id 기반 upsert
- score 입력 방식 2개 지원:
  - percent 직접 입력(score=85, max_score=None)
  - raw/max 입력(score=18, max_score=20)
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as drf_status
from rest_framework.decorators import action

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

# ✅ 단일 진실: homework_results
from apps.domains.homework_results.models import HomeworkScore

from apps.domains.homework.serializers import (
    HomeworkScoreSerializer,
    HomeworkQuickPatchSerializer,
)
from apps.domains.homework.filters import HomeworkScoreFilter

from apps.domains.results.permissions import IsTeacherOrAdmin

# submissions 기준 보정 (기존 구조 유지)
from apps.domains.submissions.models import Submission

# progress pipeline 단일 진실 (기존 구조 유지)
from apps.domains.progress.dispatcher import dispatch_progress_pipeline

# homework policy 계산 유틸 (HomeworkPolicy 단일 진실)
from apps.domains.homework.utils.homework_policy import (
    calc_homework_passed_and_clinic,
)


# =====================================================
# helpers
# =====================================================
def _safe_user_id(request) -> int | None:
    return getattr(getattr(request, "user", None), "id", None)


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

    queryset: QuerySet[HomeworkScore] = HomeworkScore.objects.select_related(
        "session",
        "session__lecture",
        "homework",
    ).all()

    serializer_class = HomeworkScoreSerializer
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

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
                    session_id=score_obj.session_id,
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
                        dispatch_progress_pipeline(sub_id)
                    except Exception:
                        pass

                transaction.on_commit(_dispatch)
                progress_info = {"dispatched": True, "reason": None}
            else:
                progress_info = {"dispatched": False, "reason": "NO_SUBMISSION"}

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
        """
        serializer = HomeworkQuickPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session_id = serializer.validated_data["session_id"]
        enrollment_id = serializer.validated_data["enrollment_id"]
        homework_id = serializer.validated_data["homework_id"]
        score = serializer.validated_data["score"]
        max_score = serializer.validated_data.get("max_score")

        with transaction.atomic():
            obj = (
                HomeworkScore.objects.select_for_update()
                .filter(
                    session_id=session_id,
                    enrollment_id=enrollment_id,
                    homework_id=homework_id,
                )
                .select_related("session", "homework")
                .first()
            )

            if obj and obj.is_locked:
                return _locked_response(obj)

            if not obj:
                obj = HomeworkScore.objects.create(
                    session_id=session_id,
                    enrollment_id=enrollment_id,
                    homework_id=homework_id,
                    score=None,
                    max_score=None,
                    updated_by_user_id=_safe_user_id(request),
                )
                obj = HomeworkScore.objects.select_related(
                    "session",
                    "homework",
                ).get(id=obj.id)

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

        return Response(
            HomeworkScoreSerializer(obj).data,
            status=drf_status.HTTP_200_OK,
        )
