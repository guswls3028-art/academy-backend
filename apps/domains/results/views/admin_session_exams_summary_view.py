# apps/domains/results/views/admin_session_exams_summary_view.py
from __future__ import annotations

from django.db.models import Avg, Min, Max, Count
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result
from apps.domains.results.serializers.session_exams_summary import (
    SessionExamsSummarySerializer,
)

from apps.domains.lectures.models import Session
from apps.domains.exams.models import Exam

from apps.domains.progress.models import SessionProgress, ClinicLink, ProgressPolicy


class AdminSessionExamsSummaryView(APIView):
    """
    ✅ Session 기준 시험 요약 API (1 Session : N Exams)

    GET /results/admin/sessions/{session_id}/exams/summary/

    원칙:
    - 점수/통계: Result 기반 (exam 단위 fact)
    - pass_rate(세션 최종 통과): SessionProgress.exam_passed 기반 (집계 결과)
    - clinic_rate: ClinicLink 기반 (진행/클리닉 분리)
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @staticmethod
    def _has_relation(model, name: str) -> bool:
        try:
            return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
        except Exception:
            return False

    @classmethod
    def _get_exams_for_session(cls, session: Session):
        # Session.exam_id (FK)
        exam_id = getattr(session, "exam_id", None)
        if exam_id:
            return list(Exam.objects.filter(id=int(exam_id)))

        # Session.exams (M2M)
        if cls._has_relation(Session, "exams"):
            try:
                return list(session.exams.all())
            except Exception:
                pass

        # Exam.sessions reverse (M2M)
        if cls._has_relation(Exam, "sessions"):
            return list(Exam.objects.filter(sessions__id=int(session.id)).distinct())

        # Exam.session reverse (FK/1:1)
        if cls._has_relation(Exam, "session"):
            return list(Exam.objects.filter(session__id=int(session.id)).distinct())

        return []

    def get(self, request, session_id: int):
        session = Session.objects.filter(id=int(session_id)).select_related("lecture").first()
        if not session:
            return Response(
                SessionExamsSummarySerializer({
                    "session_id": int(session_id),
                    "participant_count": 0,
                    "pass_rate": 0.0,
                    "clinic_rate": 0.0,
                    "strategy": "MAX",
                    "pass_source": "EXAM",
                    "exams": [],
                }).data
            )

        # 정책 (집계 전략 표시용)
        policy = ProgressPolicy.objects.filter(lecture=session.lecture).first()
        strategy = str(getattr(policy, "exam_aggregate_strategy", "MAX"))
        pass_source = str(getattr(policy, "exam_pass_source", "EXAM"))

        # session에 연결된 exams
        exams = self._get_exams_for_session(session)
        exam_ids = [int(e.id) for e in exams]

        # -----------------------------
        # session-level participant/pass/clinic
        # -----------------------------
        sp_qs = SessionProgress.objects.filter(session=session)
        participant_count = sp_qs.count()

        pass_count = sp_qs.filter(exam_passed=True).count()
        pass_rate = (pass_count / participant_count) if participant_count else 0.0

        clinic_count = (
            ClinicLink.objects.filter(session=session, is_auto=True)
            .values("enrollment_id").distinct().count()
        )
        clinic_rate = (clinic_count / participant_count) if participant_count else 0.0

        # -----------------------------
        # exam-level stats (Result 기반)
        # -----------------------------
        exam_rows = []

        for ex in exams:
            rs = Result.objects.filter(
                target_type="exam",
                target_id=int(ex.id),
            )

            agg = rs.aggregate(
                participant_count=Count("id"),
                avg_score=Avg("total_score"),
                min_score=Min("total_score"),
                max_score=Max("total_score"),
            )

            # pass 기준: pass_source가 POLICY면 정책 pass_score를 쓰지만,
            # 이 API는 "시험별 표시"가 목적이므로 여기서는 exam.pass_score를 기본 제공.
            # (정책형 pass 집계는 SessionProgress 쪽이 단일 진실)
            pass_score = float(getattr(ex, "pass_score", 0.0) or 0.0)

            # 시험 단위 pass/fail 통계는 "시험 기준선(ex.pass_score)"로 제공
            pcount = rs.filter(total_score__gte=pass_score).count()
            fcount = rs.filter(total_score__lt=pass_score).count()

            p_total = int(agg["participant_count"] or 0)
            p_rate = (pcount / p_total) if p_total else 0.0

            exam_rows.append({
                "exam_id": int(ex.id),
                "title": str(getattr(ex, "title", "") or ""),
                "pass_score": float(pass_score),

                "participant_count": p_total,
                "avg_score": float(agg["avg_score"] or 0.0),
                "min_score": float(agg["min_score"] or 0.0),
                "max_score": float(agg["max_score"] or 0.0),

                "pass_count": int(pcount),
                "fail_count": int(fcount),
                "pass_rate": round(float(p_rate), 4),
            })

        payload = {
            "session_id": int(session.id),
            "participant_count": int(participant_count),
            "pass_rate": round(float(pass_rate), 4),
            "clinic_rate": round(float(clinic_rate), 4),
            "strategy": strategy,
            "pass_source": pass_source,
            "exams": exam_rows,
        }

        return Response(SessionExamsSummarySerializer(payload).data)
