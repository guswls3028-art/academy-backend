# apps/worker/ai/problem/generator.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from academy.adapters.ai.config import AIConfig
from academy.adapters.ai.problem.prompt import BASE_PROMPT, PACKAGE_PROMPT

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore


@dataclass
class ParsedProblem:
    body: str
    choices: list
    answer: Optional[str]
    difficulty: int
    tag: str
    summary: str
    explanation: str


_client: Optional["OpenAI"] = None


def _get_client() -> "OpenAI":
    global _client
    if _client is not None:
        return _client

    if OpenAI is None:
        raise RuntimeError("openai not installed")

    cfg = AIConfig.load()
    if not cfg.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")

    _client = OpenAI(api_key=cfg.OPENAI_API_KEY)
    return _client


def generate_problem_from_ocr(ocr_text: str) -> ParsedProblem:
    cfg = AIConfig.load()

    # Quota 가드: 외부 OpenAI gpt-* 호출 카운트.
    from apps.domains.ai.services.quota import consume_ai_quota
    consume_ai_quota(kind="problem_generation")

    # PII 가드: OCR 텍스트에 답안지/Q&A 사진의 inline 전화번호가 섞여있어도
    # OpenAI로는 마스킹된 형태만 전달.
    from apps.shared.utils.pii import mask_inline_phones
    prompt = BASE_PROMPT.format(ocr_text=mask_inline_phones(ocr_text))

    client = _get_client()
    response = client.chat.completions.create(
        model=cfg.PROBLEM_GEN_MODEL,
        messages=[
            {"role": "system", "content": "당신은 교육용 시험 문제를 자동 생성하는 엔진입니다."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    # SDK 형태 차이 방어
    msg = response.choices[0].message
    content = getattr(msg, "content", None) or msg.get("content")  # type: ignore

    data = json.loads(content)

    return ParsedProblem(
        body=data.get("body", ""),
        choices=data.get("choices", []),
        answer=data.get("answer"),
        difficulty=int(data.get("difficulty", 3)),
        tag=data.get("tag", ""),
        summary=data.get("summary", ""),
        explanation=data.get("explanation", ""),
    )


def _json_from_content(content: str) -> dict:
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    return json.loads(raw)


def _normalize_generated_question(item: object, *, fallback_index: int) -> dict:
    if not isinstance(item, dict):
        return {}
    choices = item.get("choices") or []
    if not isinstance(choices, list):
        choices = [str(choices)]
    return {
        "prompt": str(item.get("prompt") or item.get("body") or "").strip(),
        "choices": [str(choice).strip() for choice in choices if str(choice).strip()],
        "answer": str(item.get("answer") or "검수 필요").strip(),
        "explanation": str(item.get("explanation") or "").strip(),
        "source_index": int(item.get("source_index") or fallback_index),
        "variant_index": int(item.get("variant_index") or 1),
    }


def generate_problem_package_from_text(
    *,
    source_text: str,
    mode: str,
    variant_count: int,
    note_policy: str,
    subject: str,
    max_questions: int,
) -> list[dict]:
    cfg = AIConfig.load()

    from apps.domains.ai.services.quota import consume_ai_quota
    consume_ai_quota(kind="problem_generation")

    from apps.shared.utils.pii import mask_inline_phones
    prompt = PACKAGE_PROMPT.format(
        source_text=mask_inline_phones(source_text),
        mode=mode,
        variant_count=variant_count,
        note_policy=note_policy,
        subject=subject or "미지정",
        max_questions=max_questions,
    )

    client = _get_client()
    response = client.chat.completions.create(
        model=cfg.PROBLEM_GEN_MODEL,
        messages=[
            {
                "role": "system",
                "content": "당신은 한국 학원 선생님이 검수할 문제지 초안을 만드는 엔진입니다.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.25,
    )

    msg = response.choices[0].message
    content = getattr(msg, "content", None) or msg.get("content")  # type: ignore
    data = _json_from_content(content or "{}")
    raw_questions = data.get("questions") if isinstance(data, dict) else []
    if not isinstance(raw_questions, list):
        return []
    questions = [
        _normalize_generated_question(item, fallback_index=index + 1)
        for index, item in enumerate(raw_questions[:max_questions])
    ]
    return [q for q in questions if q.get("prompt")]
