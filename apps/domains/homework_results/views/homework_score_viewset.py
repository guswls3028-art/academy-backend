# PATH: apps/domains/homework/views/homework_score_viewset.py

"""
HomeworkScore API (Admin / Teacher)

Endpoint:
- GET    /homework/scores/?enrollment_id=&session=&lecture=&is_locked=
- PATCH  /homework/scores/{id}/
- PATCH  /homework/scores/quick/

상태(운영 기준) — DB 표현 (고정)
- 미입력     : score=None & meta.status=None
- 미제출     : meta.status="NOT_SUBMITTED"   (0점과 다름 / 클리닉 대상)
- 0점        : score=0
- 정상 점수  : score>=0

IMPORTANT (리팩토링 금지)
- HomeworkScore 스냅샷의 단일 진실은 homework_results 도메인
- /homework/scores/* 라우팅은 프론트 편의를 위한 얇은 API
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as drf_status
from rest_framework.decorators import action

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.domains.homework_results.models import HomeworkScore
from apps.domains.homework_results.models import Homework

from apps.domains.homework.serializers import (
    HomeworkScoreSerializer,
    HomeworkQuickPatchSerializer,
)
from apps.domains.homework.filters import HomeworkScoreFilter

from apps.core.permissions import TenantResolvedAndStaff

from apps.domains.submissions.models import Submission

from apps.domains.progress.dispatcher import dispatch_progress_pipeline

from apps.domains.homework.utils.homework_policy import (
    calc_homework_passed_and_clinic,
)

from apps.domains.progress.models import ClinicLink

# 상태 판별 유틸(서버 내부 SSOT)
from apps.domains.homework_results.utils.score_status import classify_homework_score_state


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


def _normalize_status_from_request(request) -> tuple[bool, str | None]:
    """
    ✅ status 입력 처리 규칙 (기존 API 유지, 분기만 추가)

    반환:
      (status_key_present, normalized_value)

    - status 키가 없으면: (False, None)  -> 아무 변화 없음
    - status 키가 있고 null/""이면: (True, None) -> 해제
    - status 키가 있고 "NOT_SUBMITTED"이면: (True, "NOT_SUBMITTED") -> 저장
    """
    if "status" not in request.data:
        return False, None

    raw = request.data.get("status")
    if raw is None:
        return True, None

    s = str(raw).strip()
    if not s:
        return True, None

    if s == HomeworkScore.MetaStatus.NOT_SUBMITTED:
        return True, s

    # 다른 값은 허용하지 않음
    return True, "__INVALID__"


def _set_meta_status(obj: HomeworkScore, status_value: str | None) -> HomeworkScore:
    meta = obj.meta if isinstance(obj.meta, dict) else {}
    meta = {**meta}

    if status_value is None:
        # 해제
        meta.pop("status", None)
    else:
        meta["status"] = str(status_value)

    obj.meta = meta or None
    return obj


def _sync_clinic_link_for_not_submitted(
    *,
    enrollment_id: int,
    session_id: int,
    now_not_submitted: bool,
) -> None:
    """
    ✅ 클리닉 트리거 연결 (progress 파이프라인 수정 금지)

    규칙:
    - meta.status="NOT_SUBMITTED"  => 클리닉 대상 (ClinicLink 생성/유지)
    - 해제(None)                   => 본 API가 만든 "미제출" 클리닉 링크만 resolved_at 처리

    ❗Reason 확장은 migration 필요하므로 기존 AUTO_FAILED를 사용하고 meta로 kind를 구분한다.
    """
    if now_not_submitted:
        obj, created = ClinicLink.objects.get_or_create(
            enrollment_id=int(enrollment_id),
            session_id=int(session_id),
            reason=ClinicLink.Reason.AUTO_FAILED,
            defaults={
                "is_auto": True,
                "approved": False,
                "resolved_at": None,
                "meta": {
                    "kind": "HOMEWORK_NOT_SUBMITTED",
                },
            },
        )
        if not created:
            meta = obj.meta if isinstance(obj.meta, dict) else {}
            meta = {**meta}
            meta["kind"] = "HOMEWORK_NOT_SUBMITTED"
            obj.meta = meta
            obj.is_auto = True
            if getattr(obj, "resolved_at", None) is not None:
                obj.resolved_at = None
            obj.save(update_fields=["meta", "is_auto", "resolved_at", "updated_at"])
        return

    # 해제: 본 API가 만든 HOMEWORK_NOT_SUBMITTED 링크만 resolved 처리
    qs = ClinicLink.objects.filter(
        enrollment_id=int(enrollment_id),
        session_id=int(session_id),
        is_auto=True,
        resolved_at__isnull=True,
    ).exclude(meta__isnull=True)

    # meta.kind 필터는 DB마다 JSON 쿼리 지원이 달라질 수 있으므로 방어적으로 파이썬 필터링
    for link in qs.order_by("-id")[:20]:
        meta = getattr(link, "meta", None)
        if isinstance(meta, dict) and meta.get("kind") == "HOMEWORK_NOT_SUBMITTED":
            link.resolved_at = timezone.now()
            link.save(update_fields=["resolved_at", "updated_at"])


def _apply_score_and_policy(
    *,
    obj: HomeworkScore,
    score: float | None,
    max_score: float | None,
    request,
    save_fields: list[str],
) -> HomeworkScore:
    """
    HomeworkScore 점수 반영 + HomeworkPolicy 계산
    (동작 변경 없음 / 중복 제거만)

    ⚠️ meta.status="NOT_SUBMITTED" 인 경우:
    - 점수는 None으로 유지 (미제출 ≠ 0점)
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

        # meta.status까지 포함해서 프론트 디버깅/추적 가능
        hs_meta = score_obj.meta if isinstance(score_obj.meta, dict) else {}

        homework_meta.update(
            {
                "homework_score_id": score_obj.id,
                "homework_id": score_obj.homework_id,
                "score": score_obj.score,
                "max_score": score_obj.max_score,
                "passed": score_obj.passed,
                "clinic_required": score_obj.clinic_required,
                "teacher_approved": getattr(submission, "homework_teacher_approved", None),
                "status": hs_meta.get("status"),
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
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

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

        status_key_present, normalized_status = _normalize_status_from_request(request)
        if status_key_present and normalized_status == "__INVALID__":
            return Response(
                {"detail": "invalid status", "code": "INVALID", "allowed": [HomeworkScore.MetaStatus.NOT_SUBMITTED, None]},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

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

            # ✅ status 분기 (기존 API 유지)
            if status_key_present:
                score_obj = _set_meta_status(score_obj, normalized_status)

                # 미제출 저장 시: 점수는 None으로 강제(미제출 ≠ 0점)
                if normalized_status == HomeworkScore.MetaStatus.NOT_SUBMITTED:
                    next_score = None
                    next_max = None

                score_obj.save(update_fields=["meta", "updated_at"])

                # 클리닉 트리거 연결 (progress 수정 금지)
                _sync_clinic_link_for_not_submitted(
                    enrollment_id=int(score_obj.enrollment_id),
                    session_id=int(score_obj.session_id),
                    now_not_submitted=(normalized_status == HomeworkScore.MetaStatus.NOT_SUBMITTED),
                )

            # 점수/정책 계산 (기존 로직 유지)
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
                    "meta",
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
                    # ✅ progress 파이프라인 "호출만 유지" (수정 금지)
                    dispatch_progress_pipeline(submission_id=sub_id)

                transaction.on_commit(_dispatch)
                progress_info = {"dispatched": True, "reason": None}
            else:
                progress_info = {"dispatched": False, "reason": "NO_SUBMISSION"}

        data = self.get_serializer(score_obj).data

        # ✅ 프론트 편의: 상태를 명확히 구분 가능한 server-side 판별 값(응답 계약 파괴 X: meta는 그대로)
        state, _ = classify_homework_score_state(score=score_obj.score, meta=score_obj.meta)
        data["state"] = state  # optional; 기존 필드 파괴 없음
        data["progress"] = progress_info

        return Response(data, status=drf_status.HTTP_200_OK)

    # =================================================
    # PATCH /homework/scores/quick/
    # =================================================
    @action(detail=False, methods=["patch"], url_path="quick")
    def quick_patch(self, request):
        """
        Quick input (MVP)

        - % 입력: score=85, max_score 생략(percent 직접 입력)
        - raw/max: score=32, max_score=64

        ✅ 확장:
        - status="NOT_SUBMITTED" 저장/해제
        """
        serializer = HomeworkQuickPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        homework_id = serializer.validated_data["homework_id"]
        enrollment_id = serializer.validated_data["enrollment_id"]

        status_key_present, normalized_status = _normalize_status_from_request(request)
        if status_key_present and normalized_status == "__INVALID__":
            return Response(
                {"detail": "invalid status", "code": "INVALID", "allowed": [HomeworkScore.MetaStatus.NOT_SUBMITTED, None]},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        score = serializer.validated_data.get("score", None)
        max_score = serializer.validated_data.get("max_score", None)

        homework = Homework.objects.select_related("session").get(id=homework_id)
        session = homework.session

        with transaction.atomic():
            obj = (
                HomeworkScore.objects.select_for_update()
                .filter(
                    homework_id=homework_id,
                    enrollment_id=enrollment_id,
                )
                .select_related("session", "homework")
                .first()
            )

            if obj and obj.is_locked:
                return _locked_response(obj)

            if not obj:
                obj = HomeworkScore.objects.create(
                    homework=homework,
                    session=session,
                    enrollment_id=enrollment_id,
                    score=None,
                    max_score=None,
                    updated_by_user_id=_safe_user_id(request),
                )

            # ✅ status 분기 (기존 API 유지)
            if status_key_present:
                obj = _set_meta_status(obj, normalized_status)
                obj.save(update_fields=["meta", "updated_at"])

                # 미제출 저장 시: 점수 강제 None
                if normalized_status == HomeworkScore.MetaStatus.NOT_SUBMITTED:
                    score = None
                    max_score = None

                # 클리닉 트리거 연결
                _sync_clinic_link_for_not_submitted(
                    enrollment_id=int(obj.enrollment_id),
                    session_id=int(obj.session_id),
                    now_not_submitted=(normalized_status == HomeworkScore.MetaStatus.NOT_SUBMITTED),
                )

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
                    "meta",
                ],
            )

        data = HomeworkScoreSerializer(obj).data
        state, _ = classify_homework_score_state(score=obj.score, meta=obj.meta)
        data["state"] = state  # optional
        return Response(data, status=drf_status.HTTP_200_OK)
