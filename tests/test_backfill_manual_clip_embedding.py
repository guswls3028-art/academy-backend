"""Stage 6.3S — backfill_manual_clip_embedding management command 회귀 테스트.

학원장 데이터 보호 가드 검증:
- dry-run default (--apply 없으면 UPDATE 0)
- --tenant-id 필수
- meta marker idempotent 패턴
- 기존 image_embedding 값 백업 키 존재
"""
from __future__ import annotations

import pytest
from django.core.management import call_command, CommandError


def test_command_is_registered():
    """management command 등록 + import 가능."""
    from apps.domains.matchup.management.commands.backfill_manual_clip_embedding import (
        Command,
    )
    assert Command.help


def test_command_requires_tenant_id():
    """--tenant-id 누락 시 CommandError raise."""
    with pytest.raises(CommandError):
        call_command("backfill_manual_clip_embedding")


def test_command_marker_keys_match_contract():
    """marker 와 backup 키 이름이 SSOT 와 일치 — 향후 다른 곳에서 검사 시 동일 키 보장."""
    from apps.domains.matchup.management.commands import backfill_manual_clip_embedding as m
    assert m._BACKFILL_MARKER_KEY == "image_embedding_backfill_v6_3s"
    assert m._PRE_BACKUP_KEY == "image_embedding_pre_6_3s"


def test_cosine_sim_helper():
    """cosine_sim helper — None 안전, 동일 벡터 = 1.0, 다른 길이 = None."""
    from apps.domains.matchup.management.commands.backfill_manual_clip_embedding import (
        _cosine_sim,
    )
    assert _cosine_sim([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0, abs=1e-6)
    assert _cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-6)
    assert _cosine_sim(None, [1.0]) is None
    assert _cosine_sim([1.0], [1.0, 1.0]) is None
    assert _cosine_sim([], []) is None


def test_command_dry_run_default_no_apply():
    """--apply 미명시 = dry-run. argparse 동작 검증."""
    from apps.domains.matchup.management.commands.backfill_manual_clip_embedding import (
        Command,
    )
    parser = Command().create_parser("manage.py", "backfill_manual_clip_embedding")
    opts = parser.parse_args(["--tenant-id", "2", "--max-rows", "5"])
    assert opts.tenant_id == 2
    assert opts.max_rows == 5
    assert opts.apply is False
    assert opts.rerun is False
    assert opts.no_cap is False


def test_command_apply_flag_parsed():
    from apps.domains.matchup.management.commands.backfill_manual_clip_embedding import (
        Command,
    )
    parser = Command().create_parser("manage.py", "backfill_manual_clip_embedding")
    opts = parser.parse_args(["--tenant-id", "2", "--apply", "--no-cap", "--rerun"])
    assert opts.apply is True
    assert opts.no_cap is True
    assert opts.rerun is True


def test_command_problem_id_multi():
    from apps.domains.matchup.management.commands.backfill_manual_clip_embedding import (
        Command,
    )
    parser = Command().create_parser("manage.py", "backfill_manual_clip_embedding")
    opts = parser.parse_args(["--tenant-id", "2", "--problem-id", "5", "--problem-id", "7"])
    assert opts.problem_id == [5, 7]
