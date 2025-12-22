# apps/shared/contracts/ai_result.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Literal, Optional
import json
from datetime import datetime, timezone


AIJobStatus = Literal["DONE", "FAILED"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AIResult:
    """
    Worker → API 로 전달되는 '계약' (Contract)

    원칙:
    - Worker는 저장하지 않는다. 결과를 '전달'만 한다.
    - API는 결과를 받아서 results/facts 혹은 submission meta 등에 반영한다.
    """

    job_id: str
    status: AIJobStatus

    # 결과 데이터 (job type 별 스키마는 payload/result로 구분)
    result: Dict[str, Any] = None  # type: ignore

    # 오류 정보
    error: Optional[str] = None

    finished_at: str = ""

    @staticmethod
    def done(job_id: str, result: Dict[str, Any]) -> "AIResult":
        return AIResult(
            job_id=job_id,
            status="DONE",
            result=result or {},
            error=None,
            finished_at=_now_iso(),
        )

    @staticmethod
    def failed(job_id: str, error: str) -> "AIResult":
        return AIResult(
            job_id=job_id,
            status="FAILED",
            result={},
            error=error,
            finished_at=_now_iso(),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["result"] is None:
            d["result"] = {}
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "AIResult":
        return AIResult(
            job_id=str(data.get("job_id")),
            status=data.get("status"),
            result=data.get("result") or {},
            error=data.get("error"),
            finished_at=str(data.get("finished_at") or ""),
        )

    @staticmethod
    def from_json(raw: str) -> "AIResult":
        return AIResult.from_dict(json.loads(raw))
