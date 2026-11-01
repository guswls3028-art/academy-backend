"""Preserve confirmed first-attempt corrections when a retake becomes current."""

from __future__ import annotations

from typing import Any


def sync_confirmed_initial_snapshot_meta(
    *, attempt: Any, meta: dict[str, Any], total_score: float,
    max_score: float, confirmed_at: Any, fallback_source: str,
) -> dict[str, Any]:
    if not getattr(attempt, "is_initial_grade", int(attempt.attempt_index) == 1):
        return meta
    existing = meta.get("initial_snapshot")
    snapshot = dict(existing) if isinstance(existing, dict) else {}
    snapshot["total_score"] = float(total_score)
    snapshot["max_score"] = float(max_score)
    if not snapshot.get("submitted_at"):
        snapshot["submitted_at"] = confirmed_at.isoformat()
    if not snapshot.get("source"):
        snapshot["source"] = fallback_source
    return {**meta, "initial_snapshot": snapshot}
