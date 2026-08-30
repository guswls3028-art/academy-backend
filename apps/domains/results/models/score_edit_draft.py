# PATH: apps/domains/results/models/score_edit_draft.py
"""
Score Edit Draft — 임시 저장용. 최종 반영은 "편집 종료" 시 프론트가 patch API로만 수행.

- 확장 배포 중에는 사용자당 세션당 1행을 유지하고 client_id 컬럼만 준비한다.
- payload: 변경 셀 목록과 현재 선택한 과제 셀(JSON).
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
    client_id = models.CharField(max_length=128, default="", db_default="")
    # {"client_id": <tab id>, "changes": [...], "active_cell": {...}|null}.
    # Legacy list payloads remain readable during rolling deployment.
    payload = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "results_score_edit_draft"
        unique_together = (("tenant", "session", "editor_user", "client_id"),)
