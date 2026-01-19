# PATH: apps/domains/homework/views/homework_score_viewset.py
"""
HomeworkScore API (Admin / Teacher)

Endpoint:
- GET   /homework/scores/?enrollment_id=&session=&lecture=
- PATCH /homework/scores/{id}/

설계 계약 (LOCKED):
- Homework 합불(passed)은 PATCH 시점에만 계산
- SessionScores API는 passed 값을 그대로 신뢰
"""

from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as drf_status

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.domains.homework.models import HomeworkScore
from apps.domains.homework.serializers import HomeworkScoreSerializer
from apps.domains.homework.filters import HomeworkScoreFilter

from apps.domains.results.permissions import IsTeacherOrAdmin

# progress pipeline 단일 진실
from apps.domains.progress.dispatcher import dispatch_progress_pipeline

# submissions 기준 재계산
from apps.domains.submissions.models import Submission

# homework policy 계산 유틸
from apps.domains.homework.utils.homework_policy import calc_homework_passed


class HomeworkScoreViewSet(ModelViewSet):
    """
    HomeworkScore 관리 ViewSet
    """

    queryset = HomeworkScore.objects.select_related(
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

    def partial_update(self, request, *args, **kwargs):
        """
        PATCH /homework/scores/{id}

        LOCK 규칙:
        - is_locked == true → 409 CONFLICT

        성공 시:
        1) HomeworkScore 갱신
        2) Submission 보정
        3) progress pipeline 즉시 트리거
        """
        obj: HomeworkScore = self.get_object()

        # -------------------------------------------------
        # 0) LOCK 방어
        # -------------------------------------------------
        if obj.is_locked:
            return Response(
                {
                    "detail": "score block is locked",
                    "code": "LOCKED",
                    "lock_reason": obj.lock_reason,
                },
                status=drf_status.HTTP_409_CONFLICT,
            )

        # -------------------------------------------------
        # 1) HomeworkScore 업데이트
        # -------------------------------------------------
        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        incoming = dict(serializer.validated_data)

        next_score = incoming.get("score", obj.score)
        next_max = incoming.get("max_score", obj.max_score)

        # -------------------------------------------------
        # 2) Homework 합불 계산 (policy 단일 책임)
        # -------------------------------------------------
        passed = calc_homework_passed(
            session=obj.session,
            score=next_score,
            max_score=next_max,
        )

        serializer.save(
            passed=bool(passed),
            updated_by_user_id=getattr(
                getattr(request, "user", None),
                "id",
                None,
            ),
        )

        # -------------------------------------------------
        # 3) Submission 조회 (즉시 재계산 계약)
        # -------------------------------------------------
        submission = (
            Submission.objects
            .filter(
                enrollment_id=obj.enrollment_id,
                session_id=obj.session_id,
            )
            .order_by("-id")
            .first()
        )

        if not submission:
            return Response(
                {
                    "detail": (
                        "no submission found for this enrollment/session; "
                        "cannot recalculate progress"
                    ),
                    "code": "NO_SUBMISSION",
                },
                status=drf_status.HTTP_409_CONFLICT,
            )

        # -------------------------------------------------
        # 4) Submission 보정
        # -------------------------------------------------
        submission.homework_submitted = True
        submission.homework_teacher_approved = bool(
            incoming.get("teacher_approved", obj.teacher_approved)
        )

        if hasattr(submission, "meta"):
            meta = submission.meta if isinstance(submission.meta, dict) else {}
            meta = dict(meta)

            meta.setdefault("homework", {})
            if isinstance(meta["homework"], dict):
                meta["homework"].update({
                    "homework_score_id": serializer.instance.id,
                    "score": serializer.instance.score,
                    "max_score": serializer.instance.max_score,
                    "teacher_approved": submission.homework_teacher_approved,
                })

            submission.meta = meta

            submission.save(
                update_fields=[
                    "homework_submitted",
                    "homework_teacher_approved",
                    "meta",
                    "updated_at",
                ]
            )
        else:
            submission.save(
                update_fields=[
                    "homework_submitted",
                    "homework_teacher_approved",
                    "updated_at",
                ]
            )

        # -------------------------------------------------
        # 5) progress pipeline 트리거
        # -------------------------------------------------
        dispatch_progress_pipeline(int(submission.id))

        return Response(
            self.get_serializer(serializer.instance).data,
            status=drf_status.HTTP_200_OK,
        )
