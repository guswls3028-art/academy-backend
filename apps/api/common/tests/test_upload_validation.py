from __future__ import annotations

from io import BytesIO
import zipfile

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.api.common.upload_validation import (
    DEFAULT_MAX_EXCEL_SIZE,
    EXCEL_CONTENT_TYPES,
    EXCEL_EXTENSIONS,
    validate_uploaded_file,
)


def _xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["이름", "학부모전화번호", "학생전화번호"])
    worksheet.append(["업로드학생", "01070001111", ""])
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _disguised_zip_bytes() -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/not-a-workbook.txt", "not an xlsx workbook")
    return stream.getvalue()


def _broken_workbook_zip_bytes() -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types" />',
        )
        archive.writestr("xl/workbook.xml", "not workbook xml")
    return stream.getvalue()


class ExcelUploadValidationTests(SimpleTestCase):
    def _validate(self, data: bytes, *, content_type: str) -> None:
        validate_uploaded_file(
            SimpleUploadedFile("students.xlsx", data, content_type=content_type),
            allowed_extensions=EXCEL_EXTENSIONS,
            allowed_content_types=EXCEL_CONTENT_TYPES,
            max_size=DEFAULT_MAX_EXCEL_SIZE,
            label="엑셀 파일",
        )

    def test_accepts_valid_xlsx_from_supported_browser_mime_variants(self):
        data = _xlsx_bytes()

        for content_type in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/haansoftxlsx",
            "application/octet-stream",
            "",
        ):
            with self.subTest(content_type=content_type):
                self._validate(data, content_type=content_type)

    def test_rejects_valid_xlsx_with_unrelated_mime(self):
        with self.assertRaises(ValidationError) as raised:
            self._validate(_xlsx_bytes(), content_type="text/html")

        self.assertIn("MIME 형식이 허용되지 않습니다", str(raised.exception.detail["detail"]))

    def test_rejects_disguised_zip_even_with_supported_xlsx_mime(self):
        with self.assertRaises(ValidationError) as raised:
            self._validate(_disguised_zip_bytes(), content_type="application/haansoftxlsx")

        self.assertIn("파일 내용을 읽을 수 없습니다", str(raised.exception.detail["detail"]))

    def test_rejects_zip_with_broken_workbook_xml(self):
        with self.assertRaises(ValidationError) as raised:
            self._validate(_broken_workbook_zip_bytes(), content_type="application/haansoftxlsx")

        self.assertIn("파일 내용을 읽을 수 없습니다", str(raised.exception.detail["detail"]))

    def test_rejects_corrupt_xlsx_with_generic_browser_mime(self):
        with self.assertRaises(ValidationError) as raised:
            self._validate(b"not a real spreadsheet", content_type="application/octet-stream")

        self.assertIn("파일 내용을 읽을 수 없습니다", str(raised.exception.detail["detail"]))
