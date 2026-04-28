"""PII 마스킹 유틸 단위테스트."""
from apps.shared.utils.pii import (
    mask_inline_phones,
    mask_name,
    mask_phone,
    mask_sample_for_llm,
)


def test_mask_phone_korean_mobile():
    assert mask_phone("010-1234-5678") == "010-****-5678"
    assert mask_phone("01012345678") == "010-****-5678"
    assert mask_phone("010 1234 5678") == "010-****-5678"


def test_mask_phone_empty_or_short():
    assert mask_phone("") == ""
    assert mask_phone(None) == ""
    assert mask_phone("12") == "**"


def test_mask_name():
    assert mask_name("홍길동") == "홍**"
    assert mask_name("김민서") == "김**"
    assert mask_name("박이") == "박*"
    assert mask_name("A") == "*"
    assert mask_name("") == ""


def test_mask_inline_phones_in_text():
    text = "학부모 010-1234-5678에게 알림 발송"
    assert "010-****-5678" in mask_inline_phones(text)
    assert "1234" not in mask_inline_phones(text).replace("****", "")


def test_mask_sample_for_llm():
    # 전화번호 추정
    assert mask_sample_for_llm("010-1234-5678") == "010-****-5678"
    # 짧은 일반 텍스트는 그대로
    assert mask_sample_for_llm("학생") == "학생"
    # inline 전화번호는 마스킹
    out = mask_sample_for_llm("연락처: 010-1234-5678")
    assert "010-****-5678" in out


def test_mask_inline_phones_preserves_non_phone_text():
    """시험문제 텍스트(전화번호 없음)에는 변형 없음 — 임베딩 의미 보존."""
    text = "다음 중 옳지 않은 것은? 1) 가설검정 2) 표본분산 3) 정규분포"
    assert mask_inline_phones(text) == text


def test_mask_inline_phones_in_ocr_with_korean():
    """OCR 텍스트가 한국어 + 인라인 전화번호 섞인 케이스 (학생 답안지 사진)."""
    text = "이름: 홍길동 학부모 연락처 010-9876-5432 시험 문제 1번"
    out = mask_inline_phones(text)
    assert "010-****-5432" in out
    assert "9876" not in out
