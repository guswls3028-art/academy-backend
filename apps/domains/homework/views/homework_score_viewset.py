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


def _safe_user_id(request) -> int | None:
    return getattr(getattr(request, "user", None), "id", None)


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
    # ✅ 원본 구조 존중 + 방어적 처리
    if hasattr(submission, "homework_submitted"):
        submission.homework_submitted = True

    if hasattr(submission, "homework_teacher_approved") and teacher_approved is not None:
        submission.homework_teacher_approved = bool(teacher_approved)

    if hasattr(submission, "meta"):
        meta = submission.meta if isinstance(submission.meta, dict) else {}
        meta = dict(meta)

        meta.setdefault("homework", {})
        if isinstance(meta["homework"], dict):
            meta["homework"].update(
                {
                    "homework_score_id": score_obj.id,
                    "score": score_obj.score,
                    "max_score": score_obj.max_score,
                    "passed": score_obj.passed,
                    "clinic_required": score_obj.clinic_required,
                    "teacher_approved": getattr(
                        submission, "homework_teacher_approved", None
                    ),
                }
            )

        submission.meta = meta

    # update_fields 동적 구성 (필드 존재하는 것만)
    update_fields = ["updated_at"]
    for f in ["homework_submitted", "homework_teacher_approved", "meta"]:
        if hasattr(submission, f):
            update_fields.append(f)

    submission.save(update_fields=list(dict.fromkeys(update_fields)))


class HomeworkScoreViewSet(ModelViewSet):
    """
    HomeworkScore 관리 ViewSet
    """

    queryset: QuerySet[HomeworkScore] = HomeworkScore.objects.select_related(
        "session",
        "session__lecture",
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
        """
        PATCH /homework/scores/{id}/

        LOCK 규칙:
        - is_locked == true → 409 CONFLICT

        성공 시:
        1) HomeworkScore 갱신 + passed/clinic_required 계산
        2) (가능하면) Submission 보정(meta 포함)
        3) progress pipeline 트리거 (실패해도 API는 성공 유지)

        보강:
        - Submission이 없어도 409로 실패시키지 않음
          → 운영 입력 허용 (Score 저장 OK)
          → progress dispatch는 skipped 처리
        """
        obj: HomeworkScore = self.get_object()

        # -------------------------------------------------
        # 0) LOCK 방어
        # -------------------------------------------------
        if getattr(obj, "is_locked", False):
            return Response(
                {
                    "detail": "score block is locked",
                    "code": "LOCKED",
                    "lock_reason": getattr(obj, "lock_reason", None),
                },
                status=drf_status.HTTP_409_CONFLICT,
            )

        # -------------------------------------------------
        # 1) validate (partial)
        # -------------------------------------------------
        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        incoming = dict(serializer.validated_data)

        next_score = incoming.get("score", obj.score)
        next_max = incoming.get("max_score", obj.max_score)

        # -------------------------------------------------
        # 2) passed/clinic 계산 (HomeworkPolicy 단일 진실)
        # -------------------------------------------------
        passed, clinic_required, _percent = calc_homework_passed_and_clinic(
            session=obj.session,
            score=next_score,
            max_score=next_max,
        )

        # teacher_approved가 들어오면 submission에도 반영(가능할 때만)
        teacher_approved = incoming.get("teacher_approved", None)

        progress_info = {"dispatched": False, "reason": None}

        # -------------------------------------------------
        # 3) atomic: Score 저장 + (가능하면) Submission 보정
        # -------------------------------------------------
        with transaction.atomic():
            serializer.save(
                passed=bool(passed),
                clinic_required=bool(clinic_required),
                updated_by_user_id=_safe_user_id(request),
            )

            # 최신 score instance
            score_obj: HomeworkScore = serializer.instance

            # submission 조회 (있으면 보정)
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
                    teacher_approved=teacher_approved
                    if teacher_approved is not None
                    else getattr(score_obj, "teacher_approved", None),
                )

                # progress pipeline은 커밋 이후 트리거 (DB 반영 보장)
                sub_id = int(submission.id)

                def _dispatch():
                    try:
                        dispatch_progress_pipeline(sub_id)
                    except Exception:
                        # MVP: 실패해도 API는 성공 유지
                        pass

                transaction.on_commit(_dispatch)
                progress_info = {"dispatched": True, "reason": None}
            else:
                # submission이 없으면 운영 입력만 반영하고 progress는 스킵
                progress_info = {"dispatched": False, "reason": "NO_SUBMISSION"}

        data = self.get_serializer(serializer.instance).data
        data["progress"] = progress_info  # ✅ 프론트가 “왜 갱신 안 됐는지” 알 수 있게

        return Response(data, status=drf_status.HTTP_200_OK)

    # =================================================
    # PATCH /homework/scores/quick/
    # =================================================
    @action(detail=False, methods=["patch"], url_path="quick")
    def quick_patch(self, request):
        """
        Quick input (MVP, 최종)

        ✅ LOCK 존중
        - is_locked == true → 409

        ✅ 레이스 방지
        - (session_id, enrollment_id) 행을 select_for_update로 잠그고 판단

        입력 형태:
        - % 입력: score=85 (max_score 생략 가능 → 100)
        - 문항수 입력: score=32, max_score=64

        NOTE:
        - quick patch는 "조교 생산성 입력"에 집중
        - submission 보정 / progress 트리거는 partial_update에서만 수행
          (scores 탭은 refetch/invalidate로 갱신하면 충분)
        """
        serializer = HomeworkQuickPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session_id = serializer.validated_data["session_id"]
        enrollment_id = serializer.validated_data["enrollment_id"]
        score = serializer.validated_data["score"]
        max_score = serializer.validated_data.get("max_score") or 100

        with transaction.atomic():
            # 기존 row를 잠그고(lock) 확인
            existing = (
                HomeworkScore.objects.select_for_update()
                .filter(session_id=session_id, enrollment_id=enrollment_id)
                .select_related("session")
                .first()
            )

            if existing and getattr(existing, "is_locked", False):
                return Response(
                    {
                        "detail": "score block is locked",
                        "code": "LOCKED",
                        "lock_reason": getattr(existing, "lock_reason", None),
                    },
                    status=drf_status.HTTP_409_CONFLICT,
                )

            if existing:
                # update
                existing.score = score
                existing.max_score = max_score
                # passed/clinic 계산
                passed, clinic_required, _percent = calc_homework_passed_and_clinic(
                    session=existing.session,
                    score=existing.score,
                    max_score=existing.max_score,
                )
                existing.passed = bool(passed)
                existing.clinic_required = bool(clinic_required)
                existing.updated_by_user_id = _safe_user_id(request)
                existing.save(
                    update_fields=[
                        "score",
                        "max_score",
                        "passed",
                        "clinic_required",
                        "updated_by_user_id",
                        "updated_at",
                    ]
                )
                obj = existing
            else:
                # create
                obj = HomeworkScore.objects.create(
                    session_id=session_id,
                    enrollment_id=enrollment_id,
                    score=score,
                    max_score=max_score,
                    updated_by_user_id=_safe_user_id(request),
                )
                obj = HomeworkScore.objects.select_related("session").get(id=obj.id)

                passed, clinic_required, _percent = calc_homework_passed_and_clinic(
                    session=obj.session,
                    score=obj.score,
                    max_score=obj.max_score,
                )
                HomeworkScore.objects.filter(id=obj.id).update(
                    passed=bool(passed),
                    clinic_required=bool(clinic_required),
                )
                obj.refresh_from_db()

        return Response(
            HomeworkScoreSerializer(obj).data,
            status=drf_status.HTTP_200_OK,
        )
