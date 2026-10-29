import pytest

from apps.shared.utils.excel_phone import normalize_excel_phone


@pytest.mark.parametrize("value,expected", [
    ("010-0000-0000(부)", "01000000000"),
    ("０１０－１２３４－５６７８（모）", "01012345678"),
    ("[보호자]: 010 1234 5678", "01012345678"),
    ("아버지 010.1234.5678", "01012345678"),
    ("010–1234—5678 어머니", "01012345678"),
    ("(010)1234-5678", "01012345678"),
    ("01012345678 [Mother]", "01012345678"),
    (1077778888, "01077778888"),
    ("１２３４５６７８", "12345678"),
    (None, ""),
])
def test_one_phone_with_explicit_relation_is_normalized(value, expected):
    assert normalize_excel_phone(value) == expected
    assert normalize_excel_phone(expected) == expected


@pytest.mark.parametrize("value", [
    "abc01012345678", "01012345678 연락불가", "부 01012345678 모",
    "01012345678(부)/01087654321(모)", "01012345678 / 01087654321",
    "0101234567.8", "1077778888.0", "1.077778888e9", "01012345678(부2)",
    "01012345678(부]", "01012345678)", "01012345678내선1", "①012345678",
])
def test_ambiguous_or_malformed_contact_remains_invalid(value):
    assert normalize_excel_phone(value) == value
