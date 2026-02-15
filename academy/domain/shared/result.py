"""
도메인 공통: Use Case 결과 타입 (외부 라이브러리 없음)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err:
    message: str
    code: str = "error"


Result = Ok[T] | Err
