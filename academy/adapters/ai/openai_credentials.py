"""Candidate-only credential name, with explicit legacy compatibility."""
from __future__ import annotations

import os
from collections.abc import Mapping


def resolve_openai_api_key(environ: Mapping[str, str] | None = None) -> str | None:
    values = os.environ if environ is None else environ
    for name in ("ACADEMY_OPENAI_API_KEY", "OPENAI_API_KEY", "EMBEDDING_OPENAI_API_KEY"):
        value = values.get(name)
        if value and value.strip():
            return value.strip()
    return None
