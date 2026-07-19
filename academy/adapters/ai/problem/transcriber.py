from __future__ import annotations

import base64


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
) -> str:
    """Transcribe one exam page without solving or rewriting it."""
    if not data:
        return ""
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

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
