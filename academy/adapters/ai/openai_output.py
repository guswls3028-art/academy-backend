"""Completion checks shared by OpenAI-backed proposal/transcription workloads."""
from __future__ import annotations


def reasoning_options(model: str, *, effort: str = "low", chat: bool = False) -> dict:
    # Existing explicit older-model overrides retain a compatible request schema.
    if model.startswith(("gpt-5", "gpt-6")):
        return {"reasoning_effort": effort} if chat else {"reasoning": {"effort": effort}}
    return {"temperature": 0.2}


def completed_response_text(response) -> str:
    if getattr(response, "status", None) != "completed":
        raise RuntimeError("OpenAI response incomplete")
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        if getattr(item, "status", None) != "completed":
            raise RuntimeError("OpenAI message incomplete")
        if any(getattr(c, "type", None) == "refusal" for c in item.content):
            raise RuntimeError("OpenAI response refused")
    text = getattr(response, "output_text", None)
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("OpenAI response text is empty")
    return text.strip()


def completed_chat_text(response) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices or choices[0].finish_reason != "stop":
        raise RuntimeError("OpenAI response incomplete")
    message = choices[0].message
    if getattr(message, "refusal", None):
        raise RuntimeError("OpenAI response refused")
    text = getattr(message, "content", None)
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("OpenAI response text is empty")
    return text.strip()
