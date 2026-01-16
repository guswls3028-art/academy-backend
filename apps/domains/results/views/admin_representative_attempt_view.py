# ==========================================================================================
# FILE: apps/domains/results/views/admin_representative_attempt_view.py
# ==========================================================================================
"""
Admin Representative Attempt Switch

POST /results/admin/exams/<exam_id>/representative-attempt/

요청:
{
  "enrollment_id": 55,
  "attempt_id": 1234
}

==========================================================================================
✅ 목적 (Phase 2)
==========================================================================================
- Admin/Teacher가 "대표 attempt"를 수동으로 교체한다.
- Result(스냅샷)의 attempt_id도 즉시 동기화한다.
- 이후 프론트는 반드시:
    GET /results/admin/exams/<exam_id>/enrollments/<enrollment_id>/
  를 재조회하여 최신 결과를 사용한다. (진실의 원천)

==========================================================================================
✅ 프론트 계약 (중요/고정)
==========================================================================================
1) 성공 응답: Body 있음 (ok + ids)
   - 프론트가 optimistic update 하려면 최소 응답이 있는 게 안전

2) 변경 후 재계산?
   - ❌ "점수 재계산"은 하지 않는다.
   - 대표 attempt 변경은 "이미 채점된 attempt 중 선택" 행위이므로
     Result.attempt_id만 교체하면 된다.
   - 점수(total_score 등) 자체를 "attempt별로 따로 보관"하는 구조가 아니라,
     현재 설계에서는 Result가 대표 attempt의 결과 스냅샷이다.
     따라서 운영적으로는 대표 attempt 교체 시점에 Result 스냅샷이 이미 그 attempt 기반인지가 중요하다.
     (현재 시스템은 채점 완료 시 ResultApplier.apply()가 attempt_id를 덮어쓰므로
      대표 교체는 Result.attempt_id를 명시적으로 업데이트해서 일관성을 확보한다.)

3) 실패 케이스 (계약)
   - 400 INVALID: 요청 파라미터/관계 불일치(다른 exam/enrollment)
   - 404 NOT_FOUND: attempt 자체가 없음
   - 409 LOCKED: 채점 중(grading) attempt는 대표로 변경 불가 (운영 사고 방지)
   - 500 등 기타: 서버 오류

응답 형태(실패):
{
  "detail": "...",
  "code": "INVALID|NOT_FOUND|LOCKED"
}

==========================================================================================
✅ 정합성/동시성
==========================================================================================
- 동시 변경/경쟁 상황 방지:
  (exam_id, enrollment_id) 범위를 select_for_update로 잠금 후 대표 교체 수행
"""

from __future__ import annotations

from django.db import transaction

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status as drf_status
from rest_framework.exceptions import ValidationError, NotFound

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import ExamAttempt, Result


class AdminRepresentativeAttemptView(APIView):
    """
    대표 attempt 변경 (Admin/Teacher)

    NOTE:
    - 점수 재계산은 하지 않음 (대표 선택만)
    - 변경 후 프론트는 detail 재조회가 진실의 원천
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @transaction.atomic
    def post(self, request, exam_id: int):
        exam_id = int(exam_id)

        enrollment_id = request.data.get("enrollment_id")
        attempt_id = request.data.get("attempt_id")

        if enrollment_id is None or attempt_id is None:
            # 프론트 실수/누락은 400으로 명확히
            raise ValidationError({"detail": "enrollment_id and attempt_id are required", "code": "INVALID"})

        enrollment_id = int(enrollment_id)
        attempt_id = int(attempt_id)

        # -------------------------------------------------
        # 1) 대상 attempt 검증 + 잠금
        #    - 같은 exam/enrollment 범위에 대해서만 대표 교체 허용
        # -------------------------------------------------
        # 범위 잠금(경쟁 상황에서 대표 2개 되는 걸 원천 차단)
        attempts_qs = (
            ExamAttempt.objects
            .select_for_update()
            .filter(exam_id=exam_id, enrollment_id=enrollment_id)
        )

        if not attempts_qs.exists():
            # 이 enrollment이 이 exam을 응시한 적이 없는 케이스
            raise NotFound({"detail": "attempts not found for this exam/enrollment", "code": "NOT_FOUND"})

        target = attempts_qs.filter(id=attempt_id).first()
        if not target:
            # attempt_id가 다른 exam/enrollment의 것일 수 있음
            raise NotFound({"detail": "attempt not found for this exam/enrollment", "code": "NOT_FOUND"})

        # -------------------------------------------------
        # 2) LOCKED 정책 (운영 사고 방지)
        #    - grading 중인 attempt를 대표로 바꾸면 화면/통계가 흔들릴 수 있음
        # -------------------------------------------------
        if (target.status or "").lower() == "grading":
            return Response(
                {"detail": "attempt is grading; cannot switch representative", "code": "LOCKED"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        # (선택) pending도 대표 변경을 막고 싶다면 아래를 활성화
        # if (target.status or "").lower() == "pending":
        #     return Response(
        #         {"detail": "attempt is pending; cannot switch representative", "code": "LOCKED"},
        #         status=drf_status.HTTP_409_CONFLICT,
        #     )

        # -------------------------------------------------
        # 3) 대표 attempt 교체
        # -------------------------------------------------
        # 기존 대표 모두 해제
        attempts_qs.filter(is_representative=True).update(is_representative=False)

        # 선택 attempt 대표로 설정
        if not target.is_representative:
            target.is_representative = True
            target.save(update_fields=["is_representative"])

        # -------------------------------------------------
        # 4) Result 스냅샷 동기화
        # -------------------------------------------------
        # Result가 없으면 생성하지 않는다(운영상 "채점 결과가 아직 없다"는 뜻이므로)
        # 단, 프로젝트 정책상 "Result가 없더라도 대표만 바꾸고 싶다"면 get_or_create로 변경 가능.
        updated = (
            Result.objects
            .filter(
                target_type="exam",
                target_id=exam_id,
                enrollment_id=enrollment_id,
            )
            .update(attempt_id=attempt_id)
        )

        if updated == 0:
            # 대표 attempt는 존재하지만, 아직 Result 스냅샷이 없다는 의미
            # → 프론트는 detail 재조회 시 404(result not found) 가능
            # 운영/CS가 이해하기 쉽도록 명확히 409로 안내
            return Response(
                {"detail": "result snapshot not found; cannot sync representative attempt", "code": "INVALID"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        # -------------------------------------------------
        # 5) 성공 응답 (프론트 계약)
        # -------------------------------------------------
        return Response(
            {
                "ok": True,
                "exam_id": exam_id,
                "enrollment_id": enrollment_id,
                "attempt_id": attempt_id,
            },
            status=drf_status.HTTP_200_OK,
        )
