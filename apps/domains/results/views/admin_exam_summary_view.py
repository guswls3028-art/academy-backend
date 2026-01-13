# PATH: apps/domains/results/views/admin_exam_summary_view.py
"""
Admin / Teacher Exam Summary (LEGACY COMPAT)

⚠️ 원래 의도:
- 이 API는 "Session : Exam = 1:1" 가정에 의존하므로 DEPRECATED 처리(410) 했었음.

✅ 하지만 현재 프론트가 아직 /admin/exams/<exam_id>/summary/ 를 호출 중이라
410을 내리면 react-query에서 "undefined"로 터짐.

✅ 따라서 "호환 레이어"로 동작시킨다.
- 응답 스키마는 기존(AdminExamSummarySerializer) 계약 유지
- 내부 계산은 Result / ClinicLink 기반으로 안전하게 수행
- 가능한 경우 exam_id → session을 찾아 clinic_count 산출
- session을 못 찾더라도 점수/통계는 exam(Result) 기준으로 반환 가능

대체 API(신규):
  GET /results/admin/sessions/{session_id}/exams/summary/
"""

from __future__ import annotations

from django.db.models import Avg, Min, Max, Count

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result

from apps.domains.results.serializers.admin_exam_summary import (
    AdminExamSummarySerializer,
)

from apps.domains.exams.models import Exam

# ✅ clinic은 Progress가 아니라 ClinicLink 도메인이 단일 진실
from apps.domains.progress.models import ClinicLink

# Session 모델은 프로젝트마다 관계가 흔들릴 수 있어 방어적으로 접근
from apps.domains.lectures.models import Session


class AdminExamSummaryView(APIView):
    """
    GET /results/admin/exams/<exam_id>/summary/

    ✅ LEGACY 응답 스키마 유지:
    {
      participant_count,
      avg_score, min_score, max_score,
      pass_count, fail_count, pass_rate,
      clinic_count
    }
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    # -------------------------------------------------
    # helpers
    # -------------------------------------------------
    @staticmethod
    def _has_relation(model, name: str) -> bool:
        try:
            return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
        except Exception:
            return False

    @classmethod
    def _find_session_for_exam(cls, exam_id: int):
        """
        exam_id -> Session 찾기 (방어적으로)

        현재 계약(가장 유력):
        - Session.exam = ForeignKey(Exam)  => Session.exam_id 존재

        과거/프로젝트별 변형 가능성:
        - Exam.session (reverse FK/1:1)
        - Exam.sessions (M2M)
        """
        # 1) Session.exam_id (FK) - 가장 확실
        try:
            s = Session.objects.filter(exam_id=int(exam_id)).first()
            if s:
                return s
        except Exception:
            pass

        # 2) Exam.session reverse
        if cls._has_relation(Exam, "session"):
            try:
                s = Session.objects.filter(exam__id=int(exam_id)).first()
                if s:
                    return s
            except Exception:
                pass

        # 3) Exam.sessions M2M reverse
        if cls._has_relation(Exam, "sessions"):
            try:
                # Exam.objects.filter(id=exam_id, sessions__isnull=False) 형태는 모델마다 다를 수 있어 우회
                ex = Exam.objects.filter(id=int(exam_id)).first()
                if ex:
                    # session 쪽 reverse name이 default일 수도 있으니 최대한 안전하게 탐색
                    # (이 블록은 실패해도 무방)
                    if hasattr(ex, "sessions"):
                        ss = ex.sessions.all()
                        return ss.first() if ss.exists() else None
            except Exception:
                pass

        return None

    # -------------------------------------------------
    # main
    # -------------------------------------------------
    def get(self, request, exam_id: int):
        exam_id = int(exam_id)

        # -----------------------------
        # 기본 EMPTY (프론트 안정성)
        # -----------------------------
        EMPTY = {
            "participant_count": 0,
            "avg_score": 0.0,
            "min_score": 0.0,
            "max_score": 0.0,
            "pass_count": 0,
            "fail_count": 0,
            "pass_rate": 0.0,
            "clinic_count": 0,
        }

        # -----------------------------
        # Exam 로딩 (pass_score 필요)
        # -----------------------------
        exam = Exam.objects.filter(id=exam_id).first()
        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0) if exam else 0.0

        # -----------------------------
        # Result 기반 시험 통계
        # -----------------------------
        rs = Result.objects.filter(
            target_type="exam",
            target_id=exam_id,
        )

        agg = rs.aggregate(
            participant_count=Count("id"),
            avg_score=Avg("total_score"),
            min_score=Min("total_score"),
            max_score=Max("total_score"),
        )

        participant_count = int(agg["participant_count"] or 0)

        # 시험 단위 pass/fail 통계는 exam.pass_score 기준(기존 관리자 화면 의미 유지)
        pass_count = rs.filter(total_score__gte=pass_score).count() if participant_count else 0
        fail_count = rs.filter(total_score__lt=pass_score).count() if participant_count else 0
        pass_rate = (pass_count / participant_count) if participant_count else 0.0

        # -----------------------------
        # Clinic 통계 (가능하면 session 기준)
        # -----------------------------
        clinic_count = 0
        session = self._find_session_for_exam(exam_id)

        if session:
            clinic_count = (
                ClinicLink.objects.filter(session=session, is_auto=True)
                .values("enrollment_id")
                .distinct()
                .count()
            )

        payload = {
            "participant_count": participant_count,
            "avg_score": float(agg["avg_score"] or 0.0),
            "min_score": float(agg["min_score"] or 0.0),
            "max_score": float(agg["max_score"] or 0.0),
            "pass_count": int(pass_count),
            "fail_count": int(fail_count),
            "pass_rate": round(float(pass_rate), 4),
            "clinic_count": int(clinic_count),
        }

        # ✅ 프론트 계약 유지
        return Response(AdminExamSummarySerializer(payload or EMPTY).data)
