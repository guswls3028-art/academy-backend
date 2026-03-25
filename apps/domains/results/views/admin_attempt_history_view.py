# ==========================================================================================
# FILE: apps/domains/results/views/admin_attempt_history_view.py
# ==========================================================================================
"""
Admin Attempt History View

GET /results/admin/attempt-history/?enrollment_id=X&exam_id=Y
GET /results/admin/attempt-history/?enrollment_id=X&homework_id=Z

==========================================================================================
✅ 목적
==========================================================================================
- 학생 성적 드로어에서 1차, 2차, 3차... 시도 히스토리를 표시
- 시험(exam) 또는 과제(homework) 단위로 조회
- 관련 ClinicLink 정보 포함

==========================================================================================
✅ 계약 (프론트 고정)
==========================================================================================
응답 (exam):
{
  "source_type": "exam",
  "source_id": 70,
  "source_title": "영어 중간고사",
  "pass_score": 60,
  "max_score": 100,
  "attempts": [
    {"attempt_index": 1, "score": 45, "passed": false, "at": "...", "source": "grade"},
    {"attempt_index": 2, "score": 55, "passed": false, "at": "...", "source": "clinic"},
    {"attempt_index": 3, "score": 70, "passed": true, "at": "...", "source": "clinic"}
  ],
  "clinic_link_id": 42,
  "resolved": false
}

응답 (homework):
{
  "source_type": "homework",
  "source_id": 10,
  "source_title": "과제 1",
  "pass_score": 80,
  "max_score": 100,
  "attempts": [
    {"attempt_index": 1, "score": 60, "passed": false, "at": "...", "source": "grade"}
  ],
  "clinic_link_id": 43,
  "resolved": false
}
"""

from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.domains.results.permissions import IsTeacherOrAdmin


class AdminAttemptHistoryView(APIView):
    """
    Admin / Teacher 전용: enrollment + exam/homework 시도 히스토리 조회
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request):
        enrollment_id = request.query_params.get("enrollment_id")
        exam_id = request.query_params.get("exam_id")
        homework_id = request.query_params.get("homework_id")

        if not enrollment_id:
            raise ValidationError("enrollment_id is required.")

        if not exam_id and not homework_id:
            raise ValidationError("Either exam_id or homework_id is required.")

        if exam_id and homework_id:
            raise ValidationError("Provide either exam_id or homework_id, not both.")

        enrollment_id = int(enrollment_id)

        # ✅ tenant isolation: verify enrollment belongs to tenant
        from apps.domains.enrollment.models import Enrollment

        if not Enrollment.objects.filter(id=enrollment_id, tenant=request.tenant).exists():
            raise ValidationError("Enrollment not found for this tenant.")

        if exam_id:
            return self._exam_history(request, enrollment_id, int(exam_id))
        else:
            return self._homework_history(request, enrollment_id, int(homework_id))

    def _exam_history(self, request, enrollment_id: int, exam_id: int):
        from apps.domains.exams.models import Exam
        from apps.domains.results.models import ExamAttempt, Result
        from apps.domains.progress.models import ClinicLink

        # ✅ tenant isolation: verify exam belongs to tenant
        exam = Exam.objects.filter(
            id=exam_id,
            sessions__lecture__tenant=request.tenant,
        ).first()
        if not exam:
            raise ValidationError("Exam not found for this tenant.")

        pass_score = float(exam.pass_score or 0)

        # 1️⃣ 1차 점수: Result (성적 산출 SSOT)
        result = Result.objects.filter(
            target_type="exam",
            target_id=exam_id,
            enrollment_id=enrollment_id,
        ).order_by("-id").first()

        # 2️⃣ Attempt 조회
        attempts = (
            ExamAttempt.objects
            .filter(exam_id=exam_id, enrollment_id=enrollment_id)
            .order_by("attempt_index")
        )

        attempt_list = []
        for a in attempts:
            score = None
            passed = None  # 기본: 합격 기준 미설정 또는 미응시
            a_meta = a.meta or {}
            meta_status = a_meta.get("status")  # "NOT_SUBMITTED" | None

            if a.attempt_index == 1 and result:
                score = None if meta_status == "NOT_SUBMITTED" else float(result.total_score or 0)
            else:
                score = a_meta.get("total_score")

            if score is not None and pass_score > 0:
                passed = float(score) >= pass_score

            # ✅ source: clinic_link 유무 기준 (is_retake는 당일 직접 재시도에도 True)
            source = "clinic" if a.clinic_link_id else "grade"

            entry: dict = {
                "attempt_index": a.attempt_index,
                "score": score,
                "passed": passed,
                "at": a.created_at,
                "source": source,
            }
            if meta_status:
                entry["meta_status"] = meta_status
            attempt_list.append(entry)

        # 1차 결과가 있지만 ExamAttempt이 없는 경우 (레거시)
        if not attempt_list and result:
            score = float(result.total_score or 0)
            passed = score >= pass_score if pass_score > 0 else None
            attempt_list.append({
                "attempt_index": 1,
                "score": score,
                "passed": passed,
                "at": result.submitted_at or result.created_at,
                "source": "grade",
            })

        # 2️⃣ ClinicLink 조회 (최신 미해소 건)
        clinic_link = (
            ClinicLink.objects
            .filter(
                enrollment_id=enrollment_id,
                source_type="exam",
                source_id=exam_id,
            )
            .order_by("-cycle_no")
            .first()
        )

        clinic_link_id = clinic_link.id if clinic_link else None
        resolved = bool(clinic_link.resolved_at) if clinic_link else None

        return Response({
            "source_type": "exam",
            "source_id": exam_id,
            "source_title": exam.title,
            "pass_score": exam.pass_score,
            "max_score": exam.max_score,
            "attempts": attempt_list,
            "clinic_link_id": clinic_link_id,
            "resolved": resolved,
        })

    def _homework_history(self, request, enrollment_id: int, homework_id: int):
        from apps.domains.homework_results.models.homework import Homework
        from apps.domains.homework_results.models.score import HomeworkScore
        from apps.domains.homework.models.homework_policy import HomeworkPolicy
        from apps.domains.progress.models import ClinicLink

        # ✅ tenant isolation: verify homework belongs to tenant
        homework = Homework.objects.filter(
            id=homework_id,
            session__lecture__tenant=request.tenant,
        ).first()
        if not homework:
            raise ValidationError("Homework not found for this tenant.")

        # 1️⃣ HomeworkScore 조회 (all attempt_indexes)
        scores = (
            HomeworkScore.objects
            .filter(
                enrollment_id=enrollment_id,
                homework_id=homework_id,
            )
            .order_by("attempt_index")
        )

        # 2️⃣ pass_score / max_score from HomeworkPolicy
        pass_score = None
        max_score = 100  # default

        if homework.session_id:
            policy = HomeworkPolicy.objects.filter(
                tenant=request.tenant,
                session_id=homework.session_id,
            ).first()
            if policy:
                pass_score = policy.cutline_value

        # max_score: use first score's max_score if available
        first_with_max = scores.filter(max_score__isnull=False).first()
        if first_with_max and first_with_max.max_score:
            max_score = first_with_max.max_score

        attempt_list = []
        for s in scores:
            # source: "grade" for attempt_index==1, "clinic" for retakes
            source = "clinic" if s.attempt_index > 1 else "grade"

            attempt_list.append({
                "attempt_index": s.attempt_index,
                "score": s.score,
                "passed": bool(s.passed),
                "at": s.created_at,
                "source": source,
            })

        # 3️⃣ ClinicLink 조회 (최신 건)
        clinic_link = (
            ClinicLink.objects
            .filter(
                enrollment_id=enrollment_id,
                source_type="homework",
                source_id=homework_id,
            )
            .order_by("-cycle_no")
            .first()
        )

        clinic_link_id = clinic_link.id if clinic_link else None
        resolved = bool(clinic_link.resolved_at) if clinic_link else None

        return Response({
            "source_type": "homework",
            "source_id": homework_id,
            "source_title": homework.title,
            "pass_score": pass_score,
            "max_score": max_score,
            "attempts": attempt_list,
            "clinic_link_id": clinic_link_id,
            "resolved": resolved,
        })
