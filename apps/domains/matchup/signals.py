"""매치업 도메인 signal — 학원장 작성 데이터 immutable 방어막.

Stage 2 (2026-05-06): selected_problem_ids 변경 시 호출자가 명시 source 박지 않으면
pre_save 단계에서 차단한다.

원칙:
- AI callback / reanalyze / 자동 매핑 / comment 파싱 / LIKE 재매핑 = 금지 source.
- 사용자 UI / 명시 admin 복구 = 허용 source.
- update_or_create / .update() / bulk_update 는 signal 우회 가능 — 정책상
  전 세션이 들어와도 grep으로 잡힐 수 있도록 해당 호출 자체를 금지한다.

ENV flag:
- MATCHUP_SELECTION_GUARD_MODE = "strict" (default) | "warn"
  strict 모드는 미명시 source 변경도 raise. warn 모드는 logger.warning 만.
  forbidden source는 모드 무관 항상 raise.
"""
from __future__ import annotations

import logging
import os

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import MatchupHitReportEntry

logger = logging.getLogger(__name__)

FORBIDDEN_SOURCES = frozenset({
    "ai_callback",
    "reanalyze",
    "auto_recover",
    "comment_parser",
    "like_based_remap",
    "auto_remap",
    "yolo_callback",
    "vlm_callback",
})

ALLOWED_SOURCES = frozenset({
    "user_ui",
    "admin_pitr_restore",
    "admin_repair",
    "migration",
    "test",
})


class ImmutableSelectionError(ValidationError):
    """selected_problem_ids 변경이 금지/미명시 source로 시도됨."""


def _guard_mode() -> str:
    return (os.environ.get("MATCHUP_SELECTION_GUARD_MODE") or "strict").lower()


@receiver(pre_save, sender=MatchupHitReportEntry)
def guard_selected_problem_ids(sender, instance, **kwargs):
    """선택 변경 시 호출자가 _change_source 명시했는지 확인.

    호출자 contract:
        entry._change_source = "user_ui"      # or "admin_pitr_restore" etc.
        entry.selected_problem_ids = new_ids
        entry.save()

    신규 생성 (pk None) 은 통과 (생성 자체가 user_ui 의도).
    no-op (값 동일) 은 통과.
    """
    if instance.pk is None:
        return
    try:
        prev = sender.objects.only("id", "selected_problem_ids").get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    prev_sel = list(prev.selected_problem_ids or [])
    new_sel = list(instance.selected_problem_ids or [])
    if prev_sel == new_sel:
        return

    source = getattr(instance, "_change_source", None)

    if source in FORBIDDEN_SOURCES:
        raise ImmutableSelectionError(
            f"MatchupHitReportEntry(id={instance.pk}).selected_problem_ids 변경 "
            f"source='{source}' 금지 — 학원장 작성 데이터는 AI 결과로 자동 수정 불가. "
            f"Proposal 모델 또는 user_ui 경로를 사용하세요."
        )

    if source is None:
        msg = (
            f"MatchupHitReportEntry(id={instance.pk}).selected_problem_ids 변경 시 "
            f"_change_source 명시 필수 (Stage 2 immutable 원칙). "
            f"호출자가 instance._change_source = 'user_ui' 등을 박아야 함."
        )
        if _guard_mode() == "strict":
            raise ImmutableSelectionError(msg)
        logger.warning(msg)
        return

    if source not in ALLOWED_SOURCES:
        logger.warning(
            "selected_problem_ids change source=%r outside allowlist (entry=%s)",
            source, instance.pk,
        )
