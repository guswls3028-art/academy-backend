"""성적 통계 snapshot builder — Phase #13.

선생앱 1버튼 publish → 시험 1개 × 전체 수강생 익명 석차+점수 snapshot 생성.
publish 후 immutable (score 수정/학생 탈퇴 영향 X).

학생 마스킹 정책:
  - "initial": 박○○, 김민○ (성 + 동그라미)
  - "phone_last4": 박○○ (1234)
  - "pseudonym": 학생A, 학생B (랜덤 ID 또는 학원장 지정)
"""
from __future__ import annotations

from typing import Any


def _mask_initial(name: str) -> str:
    """성+나머지 동그라미. '박학생' → '박○○', '이김' → '이○'.
    빈/한자 길이별 안전 처리.
    """
    n = (name or "").strip()
    if not n:
        return "○○○"
    if len(n) == 1:
        return n + "○"
    return n[0] + "○" * (len(n) - 1)


def _mask_phone_last4(name: str, phone: str) -> str:
    """이름 마스킹 + 전번 뒷4자리. '박학생 (1234)' 형식. phone 길이 부족 시 마스킹만.
    """
    digits = "".join(c for c in (phone or "") if c.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else ""
    base = _mask_initial(name)
    if last4:
        return f"{base} ({last4})"
    return base


def _mask_pseudonym(index: int) -> str:
    """순서 기반 익명 ID. 학생A, 학생B, …, 학생Z, 학생AA …"""
    if index < 0:
        index = 0
    # Excel column letters 패턴
    letters = ""
    n = index
    while True:
        letters = chr(ord("A") + (n % 26)) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return f"학생{letters}"


def build_showcase_snapshot(
    *,
    tenant,
    exam_id: int,
    anonymization_mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """exam 단일 × 전체 수강생 익명 석차+점수 snapshot 빌드.

    Returns:
        (rows, summary) — rows: [{display_name, score, max_score, rank, total}],
                          summary: {count, avg, max, min, pass_count?, ...}

    Raises:
        ValueError — exam이 tenant에 없거나 result 데이터 없음.
    """
    from apps.domains.exams.models import Exam
    from apps.domains.results.models import Result

    # 1. tenant 격리 검증 — Exam 단일 fetch
    try:
        exam = Exam.objects.get(tenant=tenant, id=exam_id)
    except Exam.DoesNotExist:
        raise ValueError("시험이 없거나 권한이 없습니다.")

    # 2. results fetch (이 시험의 전 수강생 결과)
    qs = (
        Result.objects.filter(
            target_type="exam",
            target_id=exam.id,
            enrollment__student__tenant=tenant,
        )
        .select_related("enrollment", "enrollment__student")
    )

    raw: list[dict[str, Any]] = []
    for r in qs:
        if not r.enrollment or not r.enrollment.student:
            continue
        student = r.enrollment.student
        # 미응시(score=0 + 0/0)는 제외할지 — 학원장 의도 명확치 않음. 우선 제외 X
        # max_score=0 인 경우 제외 (시험 정의 안 됨)
        if (r.max_score or 0) <= 0:
            continue
        raw.append({
            "name": getattr(student, "name", "") or "",
            "phone": getattr(student, "phone", "") or "",
            "score": float(r.total_score or 0),
            "max_score": float(r.max_score or 0),
        })

    if not raw:
        raise ValueError("해당 시험의 채점 결과가 없습니다.")

    # 2026-05-13 안전망: phone_last4 모드는 학원에 학생 ≥3명일 때만 허용 (k-anonymity).
    # 미만이면 외부 학부모가 마스킹된 후기에서도 학생 식별 가능 → initial 모드로 자동 다운그레이드.
    if anonymization_mode == "phone_last4" and len(raw) < 3:
        anonymization_mode = "initial"

    # 3. 정렬 + rank (tie 처리: 같은 점수 = 같은 등수, 다음 등수는 인원 수만큼 건너뜀)
    raw.sort(key=lambda x: (-x["score"], x["name"]))
    prev_score = None
    prev_rank = 0
    for i, row in enumerate(raw):
        if prev_score is not None and abs(row["score"] - prev_score) < 1e-9:
            row["rank"] = prev_rank
        else:
            row["rank"] = i + 1
            prev_rank = i + 1
            prev_score = row["score"]
    total = len(raw)

    # 4. 마스킹
    mode = (anonymization_mode or "initial").strip()
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(raw):
        if mode == "phone_last4":
            display = _mask_phone_last4(row["name"], row["phone"])
        elif mode == "pseudonym":
            display = _mask_pseudonym(i)
        else:
            display = _mask_initial(row["name"])
        rows.append({
            "display_name": display,
            "score": row["score"],
            "max_score": row["max_score"],
            "rank": row["rank"],
            "total": total,
            "percent": round((row["score"] / row["max_score"]) * 100, 1) if row["max_score"] else 0.0,
        })

    # 5. summary
    scores = [r["score"] for r in raw]
    max_full = raw[0]["max_score"] if raw else 0
    summary = {
        "count": total,
        "avg": round(sum(scores) / total, 2) if total else 0.0,
        "max": max(scores) if scores else 0,
        "min": min(scores) if scores else 0,
        "max_score_full": max_full,
        "exam_title": exam.title if hasattr(exam, "title") else "",
    }
    return rows, summary
