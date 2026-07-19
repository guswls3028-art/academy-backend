from __future__ import annotations

import base64
import io

from botocore.config import Config


TRANSCRIPTION_INSTRUCTIONS = """당신은 한국 학원 시험지 원문 타이피스트입니다.
이미지에 실제로 보이는 글자만 위에서 아래, 왼쪽에서 오른쪽 순서로 옮기세요.
- 문제를 풀거나 정답을 추론하지 마세요.
- 오탈자, 번호, 기호, 단위, 줄바꿈을 임의로 교정하지 마세요.
- 수식은 한글에서 다시 편집하기 쉬운 유니코드/일반 텍스트로 최대한 보존하세요.
- 표는 행마다 | 로 구분하고, 그림은 보이는 라벨만 [그림: ...] 형식으로 적으세요.
- 읽을 수 없는 부분은 [판독불가]라고 표시하세요.
- 설명이나 머리말 없이 타이핑한 본문만 반환하세요.
"""


def transcribe_problem_image(
    data: bytes,
    *,
    mime: str,
    api_key: str,
    model: str,
    bedrock_model: str = "global.amazon.nova-2-lite-v1:0",
    bedrock_region: str = "ap-northeast-2",
) -> str:
    """Transcribe one exam page without solving or rewriting it."""
    if not data:
        return ""

    if not api_key:
        return _transcribe_with_bedrock(
            data,
            mime=mime,
            model=bedrock_model,
            region=bedrock_region,
        )

    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError("openai is not installed") from exc

    encoded = base64.b64encode(data).decode("ascii")
    client = OpenAI(api_key=api_key, timeout=90.0, max_retries=2)
    response = client.responses.create(
        model=model,
        instructions=TRANSCRIPTION_INSTRUCTIONS,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "이 시험지 페이지의 원문을 그대로 타이핑하세요."},
                {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "high"},
            ],
        }],
        max_output_tokens=6000,
    )
    return str(getattr(response, "output_text", "") or "").strip()


def _bedrock_image(data: bytes, mime: str) -> tuple[bytes, str]:
    normalized = mime.strip().lower().split(";", maxsplit=1)[0]
    formats = {
        "image/jpeg": "jpeg",
        "image/jpg": "jpeg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    if normalized in formats:
        return data, formats[normalized]

    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        converted = image.convert("RGB")
        output = io.BytesIO()
        converted.save(output, format="PNG")
    return output.getvalue(), "png"


def _transcribe_with_bedrock(
    data: bytes,
    *,
    mime: str,
    model: str,
    region: str,
) -> str:
    if not model:
        raise RuntimeError("PROBLEM_TRANSCRIPTION_BEDROCK_MODEL is not configured")

    import boto3

    image_bytes, image_format = _bedrock_image(data, mime)
    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            connect_timeout=10,
            read_timeout=90,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )
    response = client.converse(
        modelId=model,
        system=[{"text": TRANSCRIPTION_INSTRUCTIONS}],
        messages=[{
            "role": "user",
            "content": [
                {"text": "이 시험지 페이지의 원문을 그대로 타이핑하세요."},
                {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
            ],
        }],
        inferenceConfig={"maxTokens": 6000, "temperature": 0},
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(
        str(block.get("text") or "").strip()
        for block in content
        if isinstance(block, dict) and block.get("text")
    ).strip()
