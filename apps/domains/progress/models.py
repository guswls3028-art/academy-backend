# apps/domains/progress/models.py
from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Lecture, Session


class ProgressPolicy(TimestampModel):
    """
    강의별 진행/통과 정책 (커스텀 가능)
    - 영상 인정 기준
    - 시험/과제 적용 주차 범위
    - 시험 통과 점수
    - 과제 통과 방식(강사 승인 등)
    """

    class HomeworkPassType(models.TextChoices):
        SUBMIT = "SUBMIT", "제출만"
        SCORE = "SCORE", "점수"
        TEACHER_APPROVAL = "TEACHER_APPROVAL", "강사승인"

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
    exam_pass_score = models.FloatField(default=60.0)

    # ---------- Homework (n~m 주차) ----------
    homework_start_session_order = models.PositiveIntegerField(default=2)
    homework_end_session_order = models.PositiveIntegerField(default=9999)
    homework_pass_type = models.CharField(
        max_length=30,
        choices=HomeworkPassType.choices,
        default=HomeworkPassType.TEACHER_APPROVAL,
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"Policy(lecture={self.lecture_id})"


class SessionProgress(TimestampModel):
    """
    Enrollment(수강) x Session(차시) 단위의 진행 스냅샷
    - fact 도메인(lectures/submissions/results)에서 읽어와 계산된 결과를 저장
    - UI에서 '차시 통과/미통과/완료' 가시성의 원천 데이터
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
    # online일 때만 의미 있음
    video_progress_rate = models.PositiveIntegerField(default=0)  # 0~100
    video_completed = models.BooleanField(default=False)

    # ----- exam -----
    exam_score = models.FloatField(null=True, blank=True)
    exam_passed = models.BooleanField(default=False)

    # ----- homework -----
    homework_submitted = models.BooleanField(default=False)
    homework_passed = models.BooleanField(default=False)  # 강사 승인 등

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
        return f"SessionProgress(enroll={self.enrollment_id}, session={self.session_id}, completed={self.completed})"


class LectureProgress(TimestampModel):
    """
    Enrollment(수강) x Lecture(강의) 단위의 집계 스냅샷
    - list 화면(학부모/강사)에서 '한눈에 보기'를 위해 저장
    """

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
    """
    차시(Session) 기준 클리닉 연결 이력
    - 실패로 자동 생성
    - 합격자도 원하는 경우 생성 가능
    - 학원 운영의 '주 시스템' 포인트
    """

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

    memo = models.TextField(null=True, blank=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["enrollment_id", "created_at"]),
            models.Index(fields=["session", "created_at"]),
            models.Index(fields=["reason"]),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"ClinicLink(enroll={self.enrollment_id}, session={self.session_id}, reason={self.reason})"


class RiskLog(TimestampModel):
    """
    위험 판단 이력 (근거/알림 추적용)
    """

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
