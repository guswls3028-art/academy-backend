"""신고 자동 분류 (rule-based, 2026-05-12 #56).

신고 시 reason + detail + target content를 간단 keyword dict로 분류:
- "auto_dismiss": 신고 detail 비어있고 reason이 모호한 경우 (over-reporting 노이즈)
- "auto_escalate": 욕설·스팸·개인정보 명확한 키워드 매칭 시 학원장 즉시 review 권장
- "manual": 기본 — 학원장 직접 검토

학원장 admin UI에서 자동 분류 결과를 표시(badge), 다만 status는 학원장이 결정.
"""
from __future__ import annotations
from typing import Literal

TriageVerdict = Literal["auto_dismiss", "auto_escalate", "manual"]

# 욕설 / 비속어 키워드 (보수적 — false positive 최소화)
_OFFENSIVE_KW = {
    "씨발", "ㅅㅂ", "시발", "개새끼", "병신", "지랄",
    "fuck", "shit", "asshole", "fucking",
}

# 스팸 / 광고 키워드
_SPAM_KW = {
    "광고", "홍보", "할인", "이벤트", "쿠폰", "당첨", "선착순",
    "텔레그램", "카톡 추가", "DM", "비번", "비밀번호",
    "http://", "https://",  # URL 다수 포함 시 link-spam 신호
}

# 개인정보 노출 신호
_PII_KW = {
    "010-", "010 ", "주민번호", "주소", "계좌", "휴대폰", "전화번호",
}


def triage_report(reason: str, detail: str, target_excerpt: str) -> TriageVerdict:
    """신고 1건 자동 분류.

    Args:
      reason: report.reason (spam/offensive/personal_info/other)
      detail: 신고자 추가 설명
      target_excerpt: 신고 대상 글/댓글 본문 일부 (이미 plain text)

    Returns:
      "auto_dismiss" / "auto_escalate" / "manual"
    """
    reason = (reason or "").strip().lower()
    detail = (detail or "").strip()
    target = (target_excerpt or "").lower()

    # over-reporting: reason=other + detail 비어있음 + target도 짧음 → dismiss 후보
    if reason == "other" and not detail and len(target) < 5:
        return "auto_dismiss"

    # auto-escalate: reason과 매칭되는 강한 키워드 + target에서 발견
    if reason == "offensive" and any(kw.lower() in target for kw in _OFFENSIVE_KW):
        return "auto_escalate"
    if reason == "spam":
        hits = sum(1 for kw in _SPAM_KW if kw.lower() in target)
        if hits >= 2:
            return "auto_escalate"
    if reason == "personal_info" and any(kw in (target_excerpt or "") for kw in _PII_KW):
        return "auto_escalate"

    return "manual"
