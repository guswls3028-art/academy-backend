from types import SimpleNamespace
from unittest.mock import patch

import pytest

from academy.adapters.ai.problem.generator import _generate_package_content_with_bedrock


@pytest.mark.parametrize(
    ("model", "effort", "expected_effort", "expected_tokens"),
    [
        ("global.amazon.nova-2-lite-v1:0", "high", "high", None),
        ("global.amazon.nova-2-lite-v1:0", " HIGH ", "high", None),
        ("global.amazon.nova-2-lite-v1:0", "medium", "medium", 30000),
        ("global.amazon.nova-2-lite-v1:0", "low", "low", 15000),
        ("global.amazon.nova-2-lite-v1:0", "", None, 6000),
        ("us.amazon.nova-pro-v1:0", "high", None, 6000),
    ],
)
def test_bedrock_reasoning_request_contract(model, effort, expected_effort, expected_tokens):
    def converse(**request):
        # Nova 2 rejects these inference options when high reasoning is enabled.
        if expected_effort == "high":
            assert not ({"maxTokens", "temperature", "topP", "topK"}
                        & request.get("inferenceConfig", {}).keys())
        else:
            assert request["inferenceConfig"] == {"maxTokens": expected_tokens, "temperature": 0.25}
        if expected_effort:
            assert request["additionalModelRequestFields"] == {
                "reasoningConfig": {"type": "enabled", "maxReasoningEffort": expected_effort},
            }
        else:
            assert "additionalModelRequestFields" not in request
        assert request["modelId"] == model
        assert request["messages"][0]["content"][-1]["image"]["source"]["bytes"] == b"source-image"
        return {"output": {"message": {"content": [
            {"reasoningContent": {"reasoningText": {"text": "private reasoning"}}},
            {"text": '{"answer":"5"}'},
        ]}}}

    with patch("boto3.client") as factory:
        factory.return_value.converse.side_effect = converse
        result = _generate_package_content_with_bedrock(
            cfg=SimpleNamespace(PROBLEM_GEN_BEDROCK_MODEL=model, BEDROCK_REGION="ap-northeast-2"),
            system_prompt="Return a JSON proposal.", user_prompt="Review the source.",
            image_inputs=[{"index": 1, "format": "png", "data": b"source-image"}],
            reasoning_effort=effort,
        )
    assert result == '{"answer":"5"}'
