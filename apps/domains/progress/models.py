# apps/domains/progress/models.py
from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Lecture, Session


class ProgressPolicy(TimestampModel):
    """
    강의별 진행/통과 정책 (커스텀 가능)

    ✅ 1:N 시험 구조 대응 포인트
    - exam_aggregate_strategy: Session에 여러 Exam이 있을 때 Result를 어떻게 집계할지
    - exam_pass_source:
        - POLICY: ProgressPolicy.exam_pass_score 사용
        - EXAM: exams.Exam.pass_score 사용 (시험별 커트라인)
    """

    class HomeworkPassType(models.TextChoices):
        SUBMIT = "SUBMIT", "제출만"
        SCORE = "SCORE", "점수"
        TEACHER_APPROVAL = "TEACHER_APPROVAL", "강사승인"

    class ExamAggregateStrategy(models.TextChoices):
        MAX = "MAX", "최고점"
        AVG = "AVG", "평균"
        LATEST = "LATEST", "최근 제출"

    class ExamPassSource(models.TextChoices):
        POLICY = "POLICY", "정책 기준"
        EXAM = "EXAM", "시험 기준"

    lecture = models.OneToOneField(
        Lecture,
        on_delete=models.CASCADE,
        related_name="progress_policy",
    )

    # ---------- Video ----------
    video_required_rate = models.PositiveIntegerField(default=90)  # 0~100

    # ---------- Exam (n~m 주차) ----------
    exam_start_session_order = models.PositiveIntegerField(default=2)
    exam_end_session_order = models.PositiveIntegerField(default=9999)

    # (레거시/정책형 커트라인)
    exam_pass_score = models.FloatField(default=60.0)

    exam_aggregate_strategy = models.CharField(
        max_length=10,
        choices=ExamAggregateStrategy.choices,
        default=ExamAggregateStrategy.MAX,
        help_text="Session에 여러 시험이 있을 때 Result 집계 방식",
    )

    exam_pass_source = models.CharField(
        max_length=10,
        choices=ExamPassSource.choices,
        default=ExamPassSource.EXAM,
        help_text="합격 기준을 정책(POLICY)으로 볼지, 시험(EXAM)마다 볼지",
    )

    # ---------- Homework (n~m 주차) ----------
    homework_start_session_order = models.PositiveIntegerField(default=2)
    homework_end_session_order = models.PositiveIntegerField(default=9999)
    homework_pass_type = models.CharField(
        max_length=30,
        choices=HomeworkPassType.choices,
        default=HomeworkPassType.TEACHER_APPROVAL,
    )

    # ======================================================
    # ✅ STEP 1: Homework cutline/rounding 정책 (프론트 설정 가능)
    # - 초기값: cutline 80%, round_unit 5%
    # ======================================================
    homework_cutline_percent = models.PositiveIntegerField(
        default=80,
        help_text="Homework pass cutline (%). 예: 80",
    )
    homework_round_unit = models.PositiveIntegerField(
        default=5,
        help_text="Homework percent rounding unit (%). 예: 5이면 5% 단위 반올림",
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"Policy(lecture={self.lecture_id})"


class SessionProgress(TimestampModel):
    """
    Enrollment x Session 단위 진행 스냅샷
    """

    class AttendanceType(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"

    enrollment_id = models.PositiveIntegerField(db_index=True)
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="progress_rows",
    )

    # ----- attendance / video -----
    attendance_type = models.CharField(
        max_length=10,
        choices=AttendanceType.choices,
        default=AttendanceType.ONLINE,
    )
    video_progress_rate = models.PositiveIntegerField(default=0)  # 0~100
    video_completed = models.BooleanField(default=False)

    # ----- exam aggregate -----
    exam_attempted = models.BooleanField(default=False)
    exam_aggregate_score = models.FloatField(null=True, blank=True)
    exam_passed = models.BooleanField(default=False)
    exam_meta = models.JSONField(null=True, blank=True)

    # ----- homework -----
    homework_submitted = models.BooleanField(default=False)
    homework_passed = models.BooleanField(default=False)

    # ----- final -----
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    # ----- meta -----
    calculated_at = models.DateTimeField(null=True, blank=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment_id", "session"],
                name="unique_session_progress_per_enrollment",
            )
        ]
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return (
            f"SessionProgress(enroll={self.enrollment_id}, "
            f"session={self.session_id}, completed={self.completed})"
        )


class LectureProgress(TimestampModel):
    class RiskLevel(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        WARNING = "WARNING", "Warning"
        DANGER = "DANGER", "Danger"

    enrollment_id = models.PositiveIntegerField(unique=True, db_index=True)
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="lecture_progress_rows",
    )

    total_sessions = models.PositiveIntegerField(default=0)
    completed_sessions = models.PositiveIntegerField(default=0)
    failed_sessions = models.PositiveIntegerField(default=0)

    consecutive_failed_sessions = models.PositiveIntegerField(default=0)
    risk_level = models.CharField(
        max_length=10,
        choices=RiskLevel.choices,
        default=RiskLevel.NORMAL,
    )

    last_session = models.ForeignKey(
        Session,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_lecture_progress_rows",
    )
    last_updated = models.DateTimeField(null=True, blank=True)

    meta = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"LectureProgress(enroll={self.enrollment_id}, lecture={self.lecture_id}, risk={self.risk_level})"


class ClinicLink(TimestampModel):
    class Reason(models.TextChoices):
        AUTO_FAILED = "AUTO_FAILED", "자동(차시 미통과)"
        AUTO_RISK = "AUTO_RISK", "자동(위험 알림)"
        MANUAL_REQUEST = "MANUAL_REQUEST", "수동(학생/학부모 요청)"
        TEACHER_RECOMMEND = "TEACHER_RECOMMEND", "강사 추천"

    enrollment_id = models.PositiveIntegerField(db_index=True)
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="clinic_links",
    )

    reason = models.CharField(max_length=30, choices=Reason.choices)
    is_auto = models.BooleanField(default=False)
    approved = models.BooleanField(default=False)

    # ✅ 수정사항(추가): 예약 완료/분리 처리를 위한 타임스탬프
    resolved_at = models.DateTimeField(null=True, blank=True)

    memo = models.TextField(null=True, blank=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["enrollment_id", "created_at"]),
            models.Index(fields=["session", "created_at"]),
            models.Index(fields=["reason"]),
            # ✅ 수정사항(추가)
            models.Index(fields=["resolved_at"]),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"ClinicLink(enroll={self.enrollment_id}, session={self.session_id}, reason={self.reason})"


class RiskLog(TimestampModel):
    class RiskLevel(models.TextChoices):
        WARNING = "WARNING", "Warning"
        DANGER = "DANGER", "Danger"

    class Rule(models.TextChoices):
        CONSECUTIVE_INCOMPLETE = "CONSECUTIVE_INCOMPLETE", "연속 미완료"
        CONSECUTIVE_LOW_SCORE = "CONSECUTIVE_LOW_SCORE", "연속 저점수"
        OTHER = "OTHER", "기타"

    enrollment_id = models.PositiveIntegerField(db_index=True)
    session = models.ForeignKey(
        Session,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="risk_logs",
    )

    risk_level = models.CharField(max_length=10, choices=RiskLevel.choices)
    rule = models.CharField(max_length=40, choices=Rule.choices)

    reason = models.TextField(null=True, blank=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["enrollment_id", "created_at"]),
            models.Index(fields=["risk_level", "created_at"]),
            models.Index(fields=["rule", "created_at"]),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"RiskLog(enroll={self.enrollment_id}, level={self.risk_level}, rule={self.rule})"
