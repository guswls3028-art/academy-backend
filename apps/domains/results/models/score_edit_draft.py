# PATH: apps/domains/results/models/score_edit_draft.py
"""
Score Edit Draft — 임시 저장용. 최종 반영은 "편집 종료" 시 프론트가 patch API로만 수행.

- 한 사용자당 세션당 1행 (갱신 시 덮어씀).
- payload: 변경 셀 목록(JSON). 프론트 PendingChange[] 계약.
"""

from django.db import models


class ScoreEditDraft(models.Model):
    session = models.ForeignKey(
        "lectures.Session",
        on_delete=models.CASCADE,
        db_column="session_id",
        related_name="score_edit_drafts",
    )
    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        db_column="tenant_id",
        related_name="score_edit_drafts",
    )
    editor_user = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        db_column="editor_user_id",
        related_name="score_edit_drafts",
    )
    payload = models.JSONField(default=list)  # list of { type, examId?, enrollmentId, homeworkId?, score?, metaStatus? }
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "results_score_edit_draft"
        unique_together = (("tenant", "session", "editor_user"),)
