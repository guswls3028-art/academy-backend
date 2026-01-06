# apps/domains/submissions/models/submission_answer.py
from __future__ import annotations

from django.db import models
from apps.api.common.models import BaseModel


class SubmissionAnswer(BaseModel):
    """
    submissions 도메인의 문항 단위 raw 답안 (중간산물)

    🔥 NEXT-1 계약 고정 (Breaking Change)
    - exam_question_id = exams.ExamQuestion.id (절대 number 아님)  ✅ 단일 진실
    - question_number  = legacy 임시 필드 (마이그레이션/과거 데이터용)  ✅ 제거 예정

    왜?
    - question_id 같은 애매한 이름은 시스템을 무너뜨린다.
    - number 기반은 Sheet A/B 다형성에서 100% 깨진다.
    """

    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="answers",
    )

    # ✅ 최종 계약 필드: ExamQuestion.id (절대 number 아님)
    exam_question_id = models.PositiveIntegerField(
        null=True,          # ⚠️ 전환 단계 안전화: 기존 데이터가 있으므로 일단 NULL 허용
        blank=True,
        db_index=True,
        help_text="Fixed contract: exams.ExamQuestion.id (NEVER number)",
    )

    # ✅ legacy fallback: 과거 number(1,2,3...) 보관용
    question_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Legacy migration only (number). Will be removed.",
    )

    answer = models.TextField(blank=True)

    # meta는 submissions가 소유 (AI 원본/OMR 정보 저장)
    # meta 규칙 예: {"omr": {"version":"v2","detected":[...], ...}}
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "submissions_submission_answer"
        indexes = [
            models.Index(fields=["exam_question_id"]),
            models.Index(fields=["submission", "exam_question_id"]),
        ]
        # ✅ 최종적으로는 (submission, exam_question_id) unique가 정석
        # 다만 exam_question_id가 NULL인 레거시가 있을 수 있으므로
        # 현 단계에서는 unique_together를 강제하지 않는다.
        # (백필 완료 후 tighten 권장)

    def __str__(self):
        return (
            f"Submission#{self.submission_id} "
            f"Q={self.exam_question_id or f'legacy:{self.question_number}'}"
        )
