# apps/shared/contracts/ai_job.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Literal, Optional
import json
import uuid
from datetime import datetime, timezone


AIJobType = Literal[
    "ocr",
    "question_segmentation",
    "handwriting_analysis",
    "embedding",
    "problem_generation",
    "homework_video_analysis",
    "omr_grading",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AIJob:
    """
    API → Worker 로 전달되는 '계약' (Contract)

    원칙:
    - Worker는 DB/ORM/비즈니스 문맥을 몰라야 하므로
      job payload에는 file path / ids / 최소 메타만 담는다.
    """

    id: str
    type: AIJobType

    # 테넌트/스코핑 (API에서만 의미 있음. worker는 그대로 echo만)
    tenant_id: Optional[str] = None

    # 어떤 도메인 이벤트에서 발생했는지 추적용
    source_domain: Optional[str] = None  # e.g. "submissions", "exams", "homework"
    source_id: Optional[str] = None      # e.g. submission_id

    # 실제 처리에 필요한 데이터
    payload: Dict[str, Any] = None  # type: ignore

    # 추적
    created_at: str = ""

    @staticmethod
    def new(
        *,
        type: AIJobType,
        payload: Dict[str, Any],
        tenant_id: Optional[str] = None,
        source_domain: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> "AIJob":
        return AIJob(
            id=str(uuid.uuid4()),
            type=type,
            tenant_id=tenant_id,
            source_domain=source_domain,
            source_id=source_id,
            payload=payload or {},
            created_at=_now_iso(),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["payload"] is None:
            d["payload"] = {}
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "AIJob":
        return AIJob(
            id=str(data.get("id")),
            type=data.get("type"),
            tenant_id=data.get("tenant_id"),
            source_domain=data.get("source_domain"),
            source_id=data.get("source_id"),
            payload=data.get("payload") or {},
            created_at=str(data.get("created_at") or ""),
        )

    @staticmethod
    def from_json(raw: str) -> "AIJob":
        return AIJob.from_dict(json.loads(raw))
