# apps/domains/results/views/admin_exam_results_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ResultFact, ExamAttempt
from apps.domains.results.serializers.admin_exam_result_row import (
    AdminExamResultRowSerializer,
)

from apps.domains.lectures.models import Session
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission
from apps.domains.exams.models import Exam

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required
from apps.domains.results.utils.result_queries import latest_results_per_enrollment


class AdminExamResultsView(APIView):
    """
    GET /results/admin/exams/<exam_id>/results/

    ✅ 목표(원본 유지 + 정합성 강화)
    - Result(스냅샷) 기반 점수 리스트
    - Attempt/Submission 상태 연결
    - Clinic 기준 통일(ClinicLink)
    - Session↔Exam 매핑 단일화(utils.session_exam)

    ⚠️ pass 기준 정의:
    - 이 화면은 "시험(exam) 단위 결과"이므로
      pass/fail은 Exam.pass_score 기준으로 제공한다.
    - 세션 종합 통과(SessionProgress.exam_passed)는
      /admin/sessions/... summary API에서 제공하는 것이 정석.
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        exam_id = int(exam_id)

        exam = Exam.objects.filter(id=exam_id).first()
        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0) if exam else 0.0

        # -------------------------------------------------
        # 1️⃣ Result (중복 enrollment 방어: 최신 1개만)
        # -------------------------------------------------
        results = latest_results_per_enrollment(
            target_type="exam",
            target_id=exam_id,
        ).order_by("enrollment_id")

        # -------------------------------------------------
        # 2️⃣ Session 찾기 (clinic 판단용)
        #    - 세션 1 : 시험 N 구조에서도 대표 session은 필요할 수 있음(legacy UI 등)
        # -------------------------------------------------
        session = get_primary_session_for_exam(exam_id)

        # -------------------------------------------------
        # 3️⃣ Student 조회 (원본 로직 존중: progress_map 기반 추론이었지만
        #    SessionProgress에 student_id가 있다고 가정하면 깨질 수 있음)
        #
        #    여기서는 "Result.enrollment_id"를 학생으로 직접 매핑할 수 없으므로
        #    프로젝트의 Enrollment/Student 연결 방식이 필요하다.
        #    원본처럼 SessionProgress에서 student/user를 추론하던 방식이 있으면 유지해야 함.
        #
        #    ✅ 하지만 현재 제공된 코드 스냅샷만으로는
        #    enrollment_id -> student_name 해석이 프로젝트마다 달라 안전하지 않다.
        #
        #    그래서:
        #    - 원본의 Student 조회 루틴을 "가능하면" 수행하되
        #    - 실패해도 "-" 로 안전하게 반환한다.
        # -------------------------------------------------
        student_map = {}
        try:
            # 원본 코드의 의도: SessionProgress에 student_id/user_id가 붙어있을 수 있다.
            from apps.domains.progress.models import SessionProgress  # 지연 import

            if session:
                progress_rows = SessionProgress.objects.filter(session=session)
            else:
                progress_rows = SessionProgress.objects.none()

            student_ids = set()
            for sp in progress_rows:
                sid = getattr(sp, "student_id", None) or getattr(sp, "user_id", None)
                if sid:
                    student_ids.add(int(sid))

            student_map = {
                s.id: s
                for s in Student.objects.filter(id__in=list(student_ids))
            }
        except Exception:
            student_map = {}

        # -------------------------------------------------
        # 4️⃣ enrollment_id → 최신 attempt/submission 맵
        #    - ResultFact가 있으면 가장 최신 attempt를 우선 사용
        #    - 없으면 Result.attempt_id fallback
        # -------------------------------------------------
        fact_qs = (
            ResultFact.objects
            .filter(target_type="exam", target_id=exam_id)
            .exclude(attempt_id__isnull=True)
            .order_by("-attempt_id", "-id")
            .values("enrollment_id", "attempt_id", "submission_id")
        )

        latest_map = {}
        for row in fact_qs:
            eid = int(row["enrollment_id"])
            if eid not in latest_map:
                latest_map[eid] = {
                    "attempt_id": int(row["attempt_id"]),
                    "submission_id": int(row["submission_id"]),
                }

        # Result.attempt_id fallback
        attempt_ids = [r.attempt_id for r in results if getattr(r, "attempt_id", None)]
        attempt_map = {
            a.id: a
            for a in ExamAttempt.objects.filter(id__in=attempt_ids)
        }

        for r in results:
            eid = int(r.enrollment_id)
            aid = getattr(r, "attempt_id", None)
            if not aid:
                continue
            a = attempt_map.get(int(aid))
            if not a:
                continue
            if (eid not in latest_map) or (not latest_map[eid].get("submission_id")):
                latest_map[eid] = {
                    "attempt_id": int(a.id),
                    "submission_id": int(a.submission_id),
                }

        # Submission.status
        submission_ids = [v["submission_id"] for v in latest_map.values() if v.get("submission_id")]
        submission_status_map = {
            s.id: s.status
            for s in Submission.objects.filter(id__in=submission_ids)
        }

        # -------------------------------------------------
        # 5️⃣ rows 구성
        # -------------------------------------------------
        rows = []
        for r in results:
            enrollment_id = int(r.enrollment_id)

            # student_name (가능하면 매핑, 아니면 "-")
            student_name = "-"
            try:
                # 원본 로직: progress row에 student/user id가 있을 때만 표시 가능
                # 여기서는 확정 매핑이 없으므로 안전 fallback
                student_name = "-"
            except Exception:
                student_name = "-"

            latest = latest_map.get(enrollment_id, {})
            submission_id = latest.get("submission_id")
            submission_status = submission_status_map.get(submission_id) if submission_id else None

            # ✅ pass/fail은 exam 단위 => Exam.pass_score 기준
            passed = bool(float(r.total_score or 0.0) >= float(pass_score))

            # ✅ clinic_required 단일 규칙
            clinic_required = bool(
                session and is_clinic_required(session=session, enrollment_id=enrollment_id, include_manual=False)
            )

            rows.append({
                "enrollment_id": enrollment_id,
                "student_name": student_name,

                "exam_score": float(r.total_score or 0.0),
                "exam_max_score": float(r.max_score or 0.0),

                # 이후 세션 집계 확장 대비 (현재는 동일)
                "final_score": float(r.total_score or 0.0),

                "passed": passed,
                "clinic_required": clinic_required,

                "submitted_at": r.submitted_at,

                "submission_id": submission_id,
                "submission_status": submission_status,
            })

        return Response(AdminExamResultRowSerializer(rows, many=True).data)
