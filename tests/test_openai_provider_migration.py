"""Provider replacement preserves output, budgets, key secrecy and visible recovery."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from academy.adapters.ai.config import AIConfig
from academy.adapters.ai.detection import vlm_fallback as vlm
from academy.adapters.ai.openai_output import (
    completed_chat_text, completed_response_text, reasoning_options,
)


def response(text='{"answer":5}', *, status="completed", content_type="output_text"):
    result = MagicMock(status_code=200)
    result.json.return_value = {
        "status": status,
        "output": [{"type": "message", "status": "completed", "content": [
            {"type": content_type, "text": text},
        ]}],
    }
    return result


@pytest.fixture(autouse=True)
def reset_provider(monkeypatch):
    vlm.reset_doc_quota()
    vlm.reset_tenant_quota()
    vlm._reset_adapters()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai-key")
    with patch("apps.domains.ai.services.quota.consume_ai_quota"):
        yield
    vlm.reset_doc_quota()
    vlm.reset_tenant_quota()
    vlm._reset_adapters()


def test_request_uses_openai_image_schema_and_no_storage(monkeypatch):
    import requests
    post = MagicMock(return_value=response())
    monkeypatch.setattr(requests, "post", post)
    parts = [{"type": "input_text", "text": "Return JSON"},
             {"type": "input_image", "image_url": "data:image/png;base64,c3ludGhldGlj", "detail": "high"}]
    assert vlm._openai_request(model="gpt-6.1-terra", parts=parts, document_id=23, tenant_id=17) == {"answer": 5}
    assert post.call_args.args == ("https://api.openai.com/v1/responses",)
    payload = post.call_args.kwargs["json"]
    assert payload["store"] is False
    assert payload["input"][0]["content"] == parts
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["text"]["format"] == {"type": "json_object"}
    assert "temperature" not in payload
    assert "synthetic-openai-key" not in json.dumps(payload)


@pytest.mark.parametrize("result", [
    response(status="incomplete"), response(content_type="refusal"),
    response(text='{"answer":'), response(text='[]'), response(text=''),
])
def test_incomplete_refused_or_wrong_shape_never_becomes_result(monkeypatch, result):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: result)
    with pytest.raises(RuntimeError):
        vlm._openai_request(model="gpt-6-luna", parts=[])


def test_retry_counts_against_document_and_tenant_budget(monkeypatch):
    import requests
    post = MagicMock(side_effect=[MagicMock(status_code=429), response()])
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("time.sleep", lambda _: None)
    vlm._openai_request(model="gpt-6-luna", parts=[], document_id=23, tenant_id=17)
    assert post.call_count == 2
    assert vlm._doc_call_counter["23"] == 2
    assert sum(v for k, v in vlm._tenant_call_counter.items() if k[0] == "17") == 2


def test_retry_cannot_escape_tenant_cap(monkeypatch):
    import requests
    post = MagicMock(return_value=MagicMock(status_code=503))
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(vlm, "_VLM_TENANT_DAILY_LIMIT", 1)
    with pytest.raises(RuntimeError, match="한도 초과"):
        vlm._openai_request(model="gpt-6-luna", parts=[], document_id=23, tenant_id=17)
    assert post.call_count == 1


@pytest.mark.parametrize("choice", ["openai", "gemini_flash", "gemini_flash_lite"])
def test_rollout_aliases_never_select_google(choice, monkeypatch):
    monkeypatch.setenv("MATCHUP_VLM_TEXT_ADAPTER", choice)
    monkeypatch.setenv("MATCHUP_VLM_VISION_ADAPTER", choice)
    assert isinstance(vlm.get_text_adapter(), vlm.OpenAIVLMTextAdapter)
    assert vlm.get_text_adapter().model == "gpt-6-luna"
    assert vlm.get_vision_adapter().model == "gpt-6.1-terra"


def test_unknown_config_is_not_a_silent_mock(monkeypatch):
    monkeypatch.setenv("MATCHUP_VLM_TEXT_ADAPTER", "opena1")
    with pytest.raises(ValueError):
        vlm.get_text_adapter()


def test_default_models_preserve_embedding_space(monkeypatch):
    for name in ("PROBLEM_GEN_MODEL", "PROBLEM_TRANSCRIPTION_MODEL", "PROBLEM_STUDIO_EXPLANATION_MODEL"):
        monkeypatch.delenv(name, raising=False)
    cfg = AIConfig.load()
    assert cfg.PROBLEM_GEN_MODEL == cfg.PROBLEM_STUDIO_EXPLANATION_MODEL == "gpt-6.1-sol"
    assert cfg.PROBLEM_TRANSCRIPTION_MODEL == "gpt-6.1-terra"
    assert cfg.EMBEDDING_OPENAI_MODEL == "text-embedding-3-small"
    assert cfg.EMBEDDING_LOCAL_MODEL == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert reasoning_options("gpt-6.1-sol") == {"reasoning": {"effort": "low"}}
    assert reasoning_options("gpt-6-luna", chat=True) == {"reasoning_effort": "low"}
    assert "reasoning" not in reasoning_options("gpt-4.1-mini")


@pytest.mark.parametrize("status", ["incomplete", "failed", None])
def test_sdk_incomplete_text_is_not_persistable(status):
    with pytest.raises(RuntimeError, match="incomplete"):
        completed_response_text(SimpleNamespace(status=status, output_text="partial text"))


def test_sdk_refusal_and_chat_truncation_are_rejected():
    with pytest.raises(RuntimeError, match="refused"):
        completed_response_text(SimpleNamespace(status="completed", output_text="partial", output=[
            SimpleNamespace(type="message", status="completed", content=[SimpleNamespace(type="refusal")])]))
    with pytest.raises(RuntimeError, match="incomplete"):
        completed_chat_text(SimpleNamespace(choices=[SimpleNamespace(finish_reason="length")]))
    assert completed_response_text(SimpleNamespace(status="completed", output_text=" complete ")) == "complete"


def test_manual_vlm_failure_is_visible_and_tenant_budget_is_scoped():
    from apps.domains.matchup.views import DocumentPageVlmClassifyView
    from academy.adapters.ai.detection.vlm_fallback import ProblemBboxResult, PageRole
    tenant = SimpleNamespace(id=17)
    doc = SimpleNamespace(id=23)
    result = ProblemBboxResult(page_role=PageRole.PROBLEM, should_skip=False, problems=[],
                              confidence=0, debug={"adapter": "mock_after_openai_fail"})
    with patch("apps.domains.matchup.views._is_tenant_staff", return_value=True), \
         patch("apps.domains.matchup.views.MatchupDocument.objects.get", return_value=doc) as get_doc, \
         patch("apps.domains.matchup.services.ensure_document_page_images", return_value=[{"url":"synthetic-url"}]), \
         patch("academy.adapters.ai.storage.downloader.download_to_tmp", return_value="synthetic-path"), \
         patch("academy.adapters.ai.storage.downloader.cleanup_tmp_for_path") as cleanup, \
         patch("academy.adapters.ai.detection.vlm_fallback.detect_problems_vision", return_value=result) as detect:
        output = DocumentPageVlmClassifyView().post(SimpleNamespace(tenant=tenant), 23, 0)
    assert output.status_code == 502
    assert "다시 시도" in json.loads(output.content)["detail"]
    get_doc.assert_called_once_with(id=23, tenant=tenant)
    assert detect.call_args.kwargs["page_meta"]["tenant_id"] == 17
    cleanup.assert_called_once_with("synthetic-path")


@pytest.mark.parametrize("classification", [None, 1, "unknown-label"])
def test_unrecognized_crop_class_does_not_drop_a_question(monkeypatch, classification):
    from academy.adapters.ai.detection.hybrid_vlm_classifier import classify_box_image
    monkeypatch.setattr(vlm, "_openai_request", lambda **kwargs: {"classification": classification})
    assert classify_box_image(b"synthetic-image", tenant_id=17).startswith("_error:")
