"""Stage 6.3S — backfill_manual_clip_embedding management command 회귀 테스트.

학원장 데이터 보호 가드 검증:
- dry-run default (--apply 없으면 enqueue 0)
- --tenant-id 필수
- --apply도 MatchupProblem 직접 UPDATE 없이 proposal callback용 job만 enqueue
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


def test_command_has_no_direct_manual_problem_write_path():
    """운영 명령도 manual=true 원본을 직접 갱신하지 않는다."""
    import inspect
    from apps.domains.matchup.management.commands import backfill_manual_clip_embedding as module

    source = inspect.getsource(module.Command.handle)
    assert "dispatch_ai_job(" in source
    assert ".save(" not in source
    assert ".update(" not in source
    assert "image_embedding =" not in source


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
    assert opts.no_cap is False


def test_command_apply_flag_parsed():
    from apps.domains.matchup.management.commands.backfill_manual_clip_embedding import (
        Command,
    )
    parser = Command().create_parser("manage.py", "backfill_manual_clip_embedding")
    opts = parser.parse_args(["--tenant-id", "2", "--apply", "--no-cap"])
    assert opts.apply is True
    assert opts.no_cap is True


def test_command_problem_id_multi():
    from apps.domains.matchup.management.commands.backfill_manual_clip_embedding import (
        Command,
    )
    parser = Command().create_parser("manage.py", "backfill_manual_clip_embedding")
    opts = parser.parse_args(["--tenant-id", "2", "--problem-id", "5", "--problem-id", "7"])
    assert opts.problem_id == [5, 7]


def test_all_manual_reindex_commands_are_tenant_scoped_and_proposal_only():
    import inspect
    from apps.domains.matchup.management.commands import reindex_manual_problems as module

    parser = module.Command().create_parser("manage.py", "reindex_manual_problems")
    with pytest.raises(CommandError):
        parser.parse_args([])
    opts = parser.parse_args(["--tenant-id", "2"])
    assert opts.tenant_id == 2
    assert opts.apply is False

    source = inspect.getsource(module.Command.handle)
    assert "ProblemSegmentationProposal.objects.filter" in source
    assert 'proposal_kind="manual_index"' in source
    assert "dispatch_ai_job(" in source
    assert ".save(" not in source
    assert ".update(" not in source
