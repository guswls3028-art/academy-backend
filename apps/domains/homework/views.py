# PATH: apps/domains/homework/views.py
"""
HomeworkScore API

✅ Endpoint (Admin/Teacher)
- GET  /homework/scores/?enrollment_id=&session=&lecture=
- PATCH /homework/scores/{id}/

✅ PATCH 계약(프론트 고정)
- is_locked == true 인 경우 PATCH 불가
  -> 409 CONFLICT + {code:"LOCKED"}

✅ PATCH 성공 시 backend 책임(중요)
- 해당 enrollment_id + session_id에 연결된 Submission을 찾아
  - homework_submitted = True
  - homework_teacher_approved = (teacher_approved)
  - (선택) score 값을 meta에 남길 수 있음
- 그 submission_id로 progress pipeline을 즉시 트리거한다.
  -> progress.SessionProgress / LectureProgress / Risk / ClinicLink가 갱신됨

⚠️ 연결할 Submission이 없으면 "즉시 재계산" 계약을 지킬 수 없으므로
- 409 CONFLICT + {code:"NO_SUBMISSION"} 로 반환한다.
"""

from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as drf_status
from rest_framework.exceptions import ValidationError

from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from apps.domains.homework.models import HomeworkScore
from apps.domains.homework.serializers import HomeworkScoreSerializer
from apps.domains.homework.filters import HomeworkScoreFilter

# ✅ 역할 재사용 (프로젝트 user role 방어 로직)
from apps.domains.results.permissions import IsTeacherOrAdmin

# ✅ progress 파이프라인 진입점(단일 진실)
from apps.domains.progress.dispatcher import dispatch_progress_pipeline

# ✅ submissions: progress pipeline은 submission_id 기반
from apps.domains.submissions.models import Submission


class HomeworkScoreViewSet(ModelViewSet):
    queryset = HomeworkScore.objects.select_related("session", "session__lecture").all()
    serializer_class = HomeworkScoreSerializer
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = HomeworkScoreFilter
    search_fields = ["enrollment_id", "session__title", "session__lecture__title"]
    ordering_fields = ["id", "created_at", "updated_at", "is_locked", "score", "passed"]
    ordering = ["-updated_at", "-id"]

    def partial_update(self, request, *args, **kwargs):
        """
        PATCH /homework/scores/{id}/

        ✅ LOCK 규칙
        - is_locked == true -> 409

        ✅ PATCH 성공 시 backend 책임
        - submissions.Submission(homework fields) 갱신
        - progress pipeline 즉시 트리거
        """
        obj: HomeworkScore = self.get_object()

        if obj.is_locked:
            return Response(
                {
                    "detail": "score block is locked",
                    "code": "LOCKED",
                    "lock_reason": obj.lock_reason,
                },
                status=drf_status.HTTP_409_CONFLICT,
            )

        # -----------------------------
        # 1) HomeworkScore 업데이트
        # -----------------------------
        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        # 운영 편의: passed를 명시 안 주면 teacher_approved로 기본 계산
        incoming = dict(serializer.validated_data)
        teacher_approved = bool(incoming.get("teacher_approved", obj.teacher_approved))

        # score가 null이면 아직 미채점 → passed는 teacher_approved만으로 판단(초기 스캐폴딩 정책)
        # (정교한 정책은 ProgressPolicy.homework_pass_type이 책임)
        passed = bool(incoming.get("passed", teacher_approved))

        serializer.save(
            passed=passed,
            updated_by_user_id=getattr(getattr(request, "user", None), "id", None),
        )

        # -----------------------------
        # 2) 연결 Submission 찾기 (즉시 재계산 계약을 위해 필수)
        # -----------------------------
        enrollment_id = int(obj.enrollment_id)
        session_id = int(obj.session_id)

        submission = (
            Submission.objects
            .filter(enrollment_id=enrollment_id, session_id=session_id)
            .order_by("-id")
            .first()
        )

        if not submission:
            # "PATCH 성공 = 즉시 progress 재계산" 계약을 지킬 수 없으므로 실패 처리
            return Response(
                {
                    "detail": "no submission found for this enrollment/session; cannot recalculate progress",
                    "code": "NO_SUBMISSION",
                },
                status=drf_status.HTTP_409_CONFLICT,
            )

        # -----------------------------
        # 3) Submission.homework_* 갱신
        #    - progress pipeline 입력 단일 진실이 Submission이므로 여기서 보정
        # -----------------------------
        # 제출 자체가 있었다는 의미로 submitted는 True로 세팅(운영 입력이 들어왔으므로)
        submission.homework_submitted = True

        # 정책형 통과 판단의 원료값(현재 progress는 teacher_approved를 사용)
        submission.homework_teacher_approved = teacher_approved

        # score는 submissions에 필드가 없을 수 있어 meta로 보관(안전)
        meta = getattr(submission, "meta", None)
        if isinstance(meta, dict):
            new_meta = dict(meta)
        else:
            new_meta = {}

        new_meta.setdefault("homework", {})
        if isinstance(new_meta["homework"], dict):
            new_meta["homework"]["score"] = serializer.instance.score
            new_meta["homework"]["max_score"] = serializer.instance.max_score
            new_meta["homework"]["teacher_approved"] = teacher_approved
            new_meta["homework"]["homework_score_id"] = serializer.instance.id

        # meta 필드가 실제로 존재할 때만 저장
        if hasattr(submission, "meta"):
            submission.meta = new_meta
            submission.save(update_fields=["homework_submitted", "homework_teacher_approved", "meta", "updated_at"])
        else:
            submission.save(update_fields=["homework_submitted", "homework_teacher_approved", "updated_at"])

        # -----------------------------
        # 4) progress pipeline 즉시 트리거
        # -----------------------------
        dispatch_progress_pipeline(int(submission.id))

        return Response(self.get_serializer(serializer.instance).data, status=drf_status.HTTP_200_OK)
