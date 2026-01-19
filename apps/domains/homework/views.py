# PATH: apps/domains/homework/views.py
"""
HomeworkScore API

✅ Endpoint (Admin/Teacher)
- GET   /homework/scores/?enrollment_id=&session=&lecture=
- PATCH /homework/scores/{id}/

✅ 핵심 설계 계약 (LOCKED)

[PATCH 책임]
- Homework 합불(passed) 계산은 PATCH 시점에만 수행한다.
- SessionScores API는 이 값을 "그대로 신뢰"한다.

[LOCK 규칙]
- HomeworkScore.is_locked == true
  → PATCH 불가
  → 409 CONFLICT + {code:"LOCKED"}

[PATCH 성공 시 backend 책임]
1) HomeworkScore 갱신 (score / passed 등)
2) 연결 Submission 보정
   - homework_submitted = True
   - homework_teacher_approved = teacher_approved
   - (선택) meta에 score 정보 기록
3) submission_id 기준 progress pipeline 즉시 트리거
   → SessionProgress / LectureProgress / ClinicLink 등 갱신

[NO_SUBMISSION 규칙]
- enrollment_id + session_id 에 대응되는 Submission이 없으면
  → 즉시 재계산 계약을 지킬 수 없으므로
  → 409 CONFLICT + {code:"NO_SUBMISSION"}

🚫 금지
- SessionScores API에서 homework 합불/percent 계산
- progress 정책을 score API에서 직접 해석
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

# ✅ progress 파이프라인 단일 진실
from apps.domains.progress.dispatcher import dispatch_progress_pipeline

# ✅ submissions: progress는 submission_id 기준
from apps.domains.submissions.models import Submission

# ✅ homework 정책 계산 유틸 (단일 책임)
from apps.domains.homework.utils.homework_policy import calc_homework_passed


class HomeworkScoreViewSet(ModelViewSet):
    """
    HomeworkScore 관리 API (Teacher/Admin)
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
        PATCH /homework/scores/{id}/

        🔒 LOCK 규칙
        - is_locked == true → 409 CONFLICT

        📌 성공 시
        - HomeworkScore 업데이트
        - Submission 보정
        - progress pipeline 즉시 트리거
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
        # 1) HomeworkScore 업데이트 (유효성 검사)
        # -------------------------------------------------
        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        incoming = dict(serializer.validated_data)

        next_score = incoming.get("score", obj.score)
        next_max = incoming.get("max_score", obj.max_score)

        # teacher_approved는 운영 입력의 의도를 반영
        teacher_approved = bool(
            incoming.get("teacher_approved", obj.teacher_approved)
        )

        # -------------------------------------------------
        # 2) Homework 합불 계산 (단일 책임)
        # -------------------------------------------------
        passed = calc_homework_passed(
            session=obj.session,
            score=next_score,
            max_score=next_max,
            teacher_approved=teacher_approved,
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
        # 3) 연결 Submission 조회 (즉시 재계산 계약)
        # -------------------------------------------------
        enrollment_id = int(obj.enrollment_id)
        session_id = int(obj.session_id)

        submission = (
            Submission.objects
            .filter(
                enrollment_id=enrollment_id,
                session_id=session_id,
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
        # 4) Submission 보정 (progress 입력 단일 진실)
        # -------------------------------------------------
        submission.homework_submitted = True
        submission.homework_teacher_approved = bool(teacher_approved)

        # meta에 score 스냅샷 저장 (선택적, 안전)
        if hasattr(submission, "meta"):
            meta = submission.meta if isinstance(submission.meta, dict) else {}
            meta = dict(meta)

            meta.setdefault("homework", {})
            if isinstance(meta["homework"], dict):
                meta["homework"].update({
                    "homework_score_id": serializer.instance.id,
                    "score": serializer.instance.score,
                    "max_score": serializer.instance.max_score,
                    "teacher_approved": teacher_approved,
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
        # 5) progress pipeline 즉시 트리거
        # -------------------------------------------------
        dispatch_progress_pipeline(int(submission.id))

        return Response(
            self.get_serializer(serializer.instance).data,
            status=drf_status.HTTP_200_OK,
        )
