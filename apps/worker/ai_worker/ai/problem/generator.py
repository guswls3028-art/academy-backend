# apps/worker/ai/problem/generator.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from apps.worker.ai_worker.ai.config import AIConfig
from apps.worker.ai_worker.ai.problem.prompt import BASE_PROMPT

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
    prompt = BASE_PROMPT.format(ocr_text=ocr_text)

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
