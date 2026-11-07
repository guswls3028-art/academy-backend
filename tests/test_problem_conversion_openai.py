"""Exercise conversion callers through the Responses request boundary, offline."""
import base64
import copy
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.domains.tools.problem_studio import conversion_ai


QUESTION = {"prompt": "Find 2 + 3.", "choices": ["4", "5"], "answer": "", "explanation": ""}
PROPOSAL = {**QUESTION, "answer": "5", "explanation": "2 + 3 = 5", "review_flags": ["교사 확인"]}
FIGURE = {"shapes": [{"type": "line", "points": [100, 200, 300, 400]}]}


@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setenv("ACADEMY_OPENAI_API_KEY", "synthetic-conversion-key")
    monkeypatch.delenv("PROBLEM_TRANSCRIPTION_MODEL", raising=False)
    quota = Mock()
    monkeypatch.setattr(conversion_ai, "consume_tools_ai_quota", quota)
    response = SimpleNamespace(status="completed", output=[], output_text=json.dumps(PROPOSAL))

    def create_response(**request):
        # Reproduce the observed provider precondition using input messages only.
        text = "\n".join(part["text"] for message in request["input"]
                         for part in message["content"] if part["type"] == "input_text")
        if request["text"]["format"]["type"] == "json_object" and "json" not in text.lower():
            raise ValueError("JSON mode requires JSON in response input messages")
        return response

    create = Mock(side_effect=create_response)
    factory = Mock(return_value=SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=factory))
    return SimpleNamespace(factory=factory, create=create, quota=quota, response=response)


def assert_request_contract(sdk, *, thorough, image):
    sdk.factory.assert_called_once_with(api_key="synthetic-conversion-key", timeout=120, max_retries=2)
    sdk.create.assert_called_once()
    sdk.quota.assert_called_once_with(kind="problem_studio_transcription")
    request = sdk.create.call_args.kwargs
    assert request["model"] == "gpt-6.1-terra"
    assert request["store"] is False
    assert request["text"] == {"format": {"type": "json_object"}}
    assert request["reasoning"] == {"effort": "medium" if thorough else "low"}
    assert request["max_output_tokens"] == (12000 if thorough else 6000)
    assert "temperature" not in request
    assert "synthetic-conversion-key" not in json.dumps(request)
    content = request["input"][0]["content"]
    images = [part for part in content if part["type"] == "input_image"]
    assert images == ([{"type": "input_image", "detail": "high",
                       "image_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")}]
                     if image else [])
    return "\n".join(part["text"] for part in content if part["type"] == "input_text")


@pytest.mark.parametrize("thorough", [False, True])
@pytest.mark.parametrize("image", [b"synthetic-page", b""])
def test_proposal_keeps_teacher_data_and_satisfies_json_input_contract(sdk, thorough, image):
    original = copy.deepcopy(QUESTION)
    instruction = "원문은 유지하고 정답과 해설을 작성하세요."
    proposal = conversion_ai.propose_question(QUESTION, image, "image/png", instruction=instruction, thorough=thorough)
    text = assert_request_contract(sdk, thorough=thorough, image=image)
    assert json.dumps({"teacher_instruction": instruction,
                       "question": {key: QUESTION.get(key) for key in
                                    ("prompt", "choices", "answer", "explanation", "original", "source_number")}},
                      ensure_ascii=False) in text
    assert proposal["answer"] == "5"
    assert proposal["review_flags"] == ["교사 확인"]
    assert proposal["reviewed"] is False
    assert QUESTION == original


def test_first_redraw_request_satisfies_json_input_contract(sdk):
    sdk.response.output_text = json.dumps(FIGURE)
    assert conversion_ai.redraw_figure(b"synthetic-crop", "image/png").startswith(b"\x89PNG")
    text = assert_request_contract(sdk, thorough=True, image=b"synthetic-crop")
    assert "첨부한 원본 그림" in text


@pytest.mark.parametrize("caller", ["proposal", "redraw"])
@pytest.mark.parametrize("failure", ["provider", "incomplete", "refused"])
def test_provider_failures_and_noncompleted_output_never_become_proposals(sdk, caller, failure):
    if failure == "provider":
        sdk.create.side_effect = RuntimeError("synthetic provider unavailable")
    elif failure == "incomplete":
        sdk.response.status = "incomplete"
    else:
        sdk.response.output = [SimpleNamespace(type="message", status="completed",
                                               content=[SimpleNamespace(type="refusal")])]
    with pytest.raises(RuntimeError):
        if caller == "proposal":
            conversion_ai.propose_question(QUESTION, b"synthetic", "image/png", instruction="정답을 확인하세요.")
        else:
            conversion_ai.redraw_figure(b"synthetic", "image/png")
    assert sdk.create.call_count == 1
    assert QUESTION["answer"] == ""
