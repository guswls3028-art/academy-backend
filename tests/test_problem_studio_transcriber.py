from __future__ import annotations

from io import BytesIO
from unittest.mock import Mock, patch

from PIL import Image

from academy.adapters.ai.problem.transcriber import transcribe_problem_image


def _png_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@patch("boto3.client")
def test_transcriber_uses_bedrock_when_openai_key_is_absent(client_factory: Mock) -> None:
    client = client_factory.return_value
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": "1. x² = 4"}]}}
    }

    text = transcribe_problem_image(
        _png_bytes(),
        mime="image/png",
        api_key="",
        model="unused-openai-model",
        bedrock_model="global.amazon.nova-2-lite-v1:0",
        bedrock_region="ap-northeast-2",
    )

    assert text == "1. x² = 4"
    client_factory.assert_called_once()
    call = client.converse.call_args.kwargs
    assert call["modelId"] == "global.amazon.nova-2-lite-v1:0"
    assert call["messages"][0]["content"][1]["image"]["format"] == "png"
    assert call["messages"][0]["content"][1]["image"]["source"]["bytes"]


@patch("boto3.client")
def test_transcriber_normalizes_bmp_for_bedrock(client_factory: Mock) -> None:
    client_factory.return_value.converse.return_value = {
        "output": {"message": {"content": [{"text": "본문"}]}}
    }
    image = Image.new("RGB", (8, 8), "white")
    source = BytesIO()
    image.save(source, format="BMP")

    text = transcribe_problem_image(
        source.getvalue(),
        mime="image/bmp",
        api_key="",
        model="unused-openai-model",
    )

    assert text == "본문"
    image_payload = client_factory.return_value.converse.call_args.kwargs["messages"][0]["content"][1]["image"]
    assert image_payload["format"] == "png"
    assert image_payload["source"]["bytes"].startswith(b"\x89PNG")
