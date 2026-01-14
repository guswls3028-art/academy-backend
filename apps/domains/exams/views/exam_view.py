# PATH: apps/domains/exams/views/exam_view.py

from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer
from apps.domains.lectures.models import Session


class ExamViewSet(ModelViewSet):
    """
    ✅ Exam 도메인의 '유일한 생성 진입점'

    ===============================
    📌 이 ViewSet의 책임
    ===============================
    - Exam 자체를 생성/조회/수정한다
    - Exam의 **정체성은 exam.id (PK)** 로만 정의된다
    - session은 '소속 정보'일 뿐, exam의 식별자가 아님

    ===============================
    📌 중요한 설계 원칙 (절대 깨면 안 됨)
    ===============================
    1. examId는 생성 시점에 고정된다 (전 도메인 공통 키)
    2. results / sessions / analytics 는 examId 기준으로 동작
    3. session ↔ exam 관계는 조회/필터 용도이지
       "시험의 정체성"을 결정하지 않는다

    👉 즉:
    - 프론트는 examId만 믿고 사용하면 된다
    - session 구조가 바뀌어도 examId는 절대 흔들리면 안 된다
    """

    queryset = Exam.objects.all()
    serializer_class = ExamSerializer
    permission_classes = [IsAuthenticated]

    # ======================================================
    # CREATE
    # ======================================================
    def perform_create(self, serializer):
        """
        ===============================
        ✅ Exam 생성 규칙 (고정 계약)
        ===============================

        ✔ 프론트에서 반드시 session_id를 전달해야 한다
        ✔ Exam 모델에는 session 필드를 직접 쓰지 않는다
        ✔ subject는 session → lecture → subject 기준으로
          백엔드가 자동 결정한다

        -------------------------------
        ❗ 왜 session_id를 여기서 받는가?
        -------------------------------
        - Exam은 항상 "어느 수업/차시에서 만들어졌는지"를
          명시적으로 알아야 한다
        - 하지만 exam의 PK(exam.id)는
          session과 **논리적으로 분리**되어야 한다

        👉 생성 시점에만 session을 사용하고,
           이후 모든 연산은 examId 기준으로 진행한다
        """

        session_id = self.request.data.get("session_id")
        if not session_id:
            raise ValidationError({"session_id": "session_id is required"})

        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            raise ValidationError({"session_id": "session_id must be integer"})

        try:
            # 🔥 여기서만 Session을 신뢰한다
            session = Session.objects.select_related("lecture").get(id=session_id)
        except Session.DoesNotExist:
            raise ValidationError({"session_id": "invalid session_id"})

        # --------------------------------------------------
        # 1️⃣ Exam 생성 (아직 session 연결 ❌)
        # --------------------------------------------------
        # ⚠️ 매우 중요:
        # - 이 시점에서 생성되는 exam.id가
        #   시스템 전체에서 사용하는 '유일한 시험 식별자'
        exam = serializer.save(
            subject=session.lecture.subject
        )

        # --------------------------------------------------
        # 2️⃣ session ↔ exam 관계 연결
        # --------------------------------------------------
        # ✔ ManyToMany 구조 (현재 구조)
        # ✔ 혹은 legacy OneToMany 구조 대응
        #
        # ❗ 이 관계는:
        # - 조회 / 필터 / 그룹핑 용도일 뿐
        # - examId의 의미를 바꾸지 않는다
        if hasattr(exam, "sessions"):
            exam.sessions.add(session)
        elif hasattr(exam, "session"):
            exam.session = session
            exam.save(update_fields=["session"])

    # ======================================================
    # QUERY FILTERS
    # ======================================================
    @staticmethod
    def _has_relation(model, name: str) -> bool:
        """
        모델에 특정 relation/field가 존재하는지 안전하게 확인

        👉 이유:
        - 프로젝트 히스토리상
          Exam.session / Exam.sessions 구조가 혼재했음
        - 런타임에서 구조를 유연하게 대응하기 위함
        """
        try:
            return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
        except Exception:
            return False

    def get_queryset(self):
        """
        ===============================
        ✅ Exam 조회 필터
        ===============================

        ✔ GET /exams/?session_id=123
        ✔ GET /exams/?lecture_id=10

        -------------------------------
        ❗ 매우 중요한 보장
        -------------------------------
        - 이 필터들은 "조회 편의"를 위한 것
        - exam의 정체성(examId)을 변경하거나
          프론트 로직에 영향을 주지 않는다

        👉 프론트는:
        - examId만 신뢰
        - session_id는 조회 조건으로만 사용
        """

        qs = super().get_queryset()

        session_id = self.request.query_params.get("session_id")
        if session_id:
            sid = int(session_id)

            if self._has_relation(Exam, "sessions"):
                qs = qs.filter(sessions__id=sid)
            elif self._has_relation(Exam, "session"):
                qs = qs.filter(session__id=sid)
            else:
                return qs.none()

        lecture_id = self.request.query_params.get("lecture_id")
        if lecture_id:
            lid = int(lecture_id)

            if self._has_relation(Exam, "sessions"):
                qs = qs.filter(sessions__lecture_id=lid)
            elif self._has_relation(Exam, "session"):
                qs = qs.filter(session__lecture_id=lid)
            else:
                return qs.none()

        # ✔ 중복 제거 + 최신순
        return qs.distinct().order_by("-created_at")
