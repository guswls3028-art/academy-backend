from pathlib import Path

import pytest
from openpyxl import Workbook

from academy.application.services.excel_parsing_service import (
    ExcelValidationError,
    parse_student_excel_file,
)


def _write_student_excel(path: Path, *, name: str, parent_phone: str = "01031217466") -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["이름", "학부모전화번호", "학생전화번호", "학교", "학년", "성별", "메모"])
    ws.append([name, parent_phone, "", "테스트고", 1, "M", "parser-regression"])
    wb.save(path)


def test_parse_student_excel_allows_long_name_when_valid_phone_exists(tmp_path):
    path = tmp_path / "students.xlsx"
    name = "E2E-ALIM-0523115013학생"
    assert len(name) > 20
    _write_student_excel(path, name=name)

    rows, _lecture_title = parse_student_excel_file(str(path))

    assert len(rows) == 1
    assert rows[0]["name"] == name
    assert rows[0]["parent_phone"] == "01031217466"


def test_parse_student_excel_remains_strict_without_partial_error_collector(tmp_path):
    path = tmp_path / "invalid-students.xlsx"
    _write_student_excel(path, name="오류학생", parent_phone="번호오류")

    with pytest.raises(ExcelValidationError) as exc_info:
        parse_student_excel_file(str(path))

    assert exc_info.value.errors[0]["row"] == 2
