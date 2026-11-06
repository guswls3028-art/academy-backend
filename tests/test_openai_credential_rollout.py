"""Exercise actual key consumers and the immutable pre-rollout config snapshot."""
import hashlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from academy.adapters.ai.config import AIConfig
from academy.adapters.ai.openai_credentials import resolve_openai_api_key

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("values,expected", [
    ({}, None), ({"OPENAI_API_KEY": "legacy"}, "legacy"),
    ({"EMBEDDING_OPENAI_API_KEY": "embedding"}, "embedding"),
    ({"ACADEMY_OPENAI_API_KEY": " candidate ", "OPENAI_API_KEY": "legacy", "EMBEDDING_OPENAI_API_KEY": "embedding"}, "candidate"),
    ({"ACADEMY_OPENAI_API_KEY": " ", "OPENAI_API_KEY": "legacy"}, "legacy"),
])
def test_dedicated_key_precedence_and_explicit_legacy_compatibility(values, expected):
    assert resolve_openai_api_key(values) == expected


def test_old_image_config_ignores_new_key_but_candidate_uses_it(monkeypatch):
    # Verbatim source, not a reimplementation: 1b609:academy/adapters/ai/config.py.
    # Kept as data because CI test checkouts can be shallow.
    fixture = ROOT / "tests/fixtures/ai_config_1b609.py.txt"
    raw = fixture.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == "7921c73afcce13317ebe098c53651902a680821b"
    loader = importlib.machinery.SourceFileLoader("legacy_ai_config", str(fixture))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, loader.name, module)
    loader.exec_module(module)
    monkeypatch.setenv("ACADEMY_OPENAI_API_KEY", "candidate-only-synthetic")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_BACKEND", "local")
    assert module.AIConfig.load().OPENAI_API_KEY is None
    assert module.AIConfig.load().EMBEDDING_BACKEND == "local"
    assert AIConfig.load().OPENAI_API_KEY == "candidate-only-synthetic"
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-unchanged")
    assert module.AIConfig.load().OPENAI_API_KEY == "legacy-unchanged"
    assert AIConfig.load().OPENAI_API_KEY == "candidate-only-synthetic"


def test_matchup_gate_and_excel_call_use_dedicated_key(monkeypatch):
    from academy.application.use_cases.ai.pipelines import matchup_pipeline
    from academy.adapters.ai.excel_schema_infer import infer_parent_phone_column
    monkeypatch.setenv("ACADEMY_OPENAI_API_KEY", "dedicated-synthetic")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_OPENAI_API_KEY", "")
    monkeypatch.setenv("MATCHUP_VLM_VISION_ADAPTER", "openai")
    monkeypatch.setenv("MATCHUP_VLM_TEXT_ADAPTER", "gemini_flash")
    assert matchup_pipeline._real_vlm_vision_configured()
    assert matchup_pipeline._real_vlm_text_configured()
    factory = MagicMock()
    factory.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(refusal=None, content='{"parent_phone_col_index":1,"confidence":0.9}'))])
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=factory))
    with patch("apps.domains.ai.services.quota.consume_ai_quota"):
        assert infer_parent_phone_column([{"col_index": 1, "header": "Parent", "samples": []}]) == (1, 0.9)
    assert factory.call_args.kwargs["api_key"] == "dedicated-synthetic"
    assert factory.return_value.chat.completions.create.call_args.kwargs["store"] is False


def test_all_consuming_image_requirement_graphs_install_sdk():
    def dependencies(path):
        found = set()
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.startswith("-r "):
                found.update(dependencies(path.parent / line[3:].strip()))
            elif line and not line.startswith("-"):
                found.add(line)
        return found
    for manifest in ("api.txt", "worker-tools.txt", "worker-ai-cpu.txt"):
        assert "openai" in dependencies(ROOT / "requirements" / manifest)
