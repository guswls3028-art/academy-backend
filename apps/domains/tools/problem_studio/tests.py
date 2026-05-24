from __future__ import annotations

import zipfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.domains.tools.problem_studio.services import build_problem_studio_package, extract_source


def _zip_file(name: str, files: dict[str, str]) -> SimpleUploadedFile:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return SimpleUploadedFile(name, buf.getvalue())


class ProblemStudioServiceTests(SimpleTestCase):
    def test_extracts_hwpx_preview_text(self):
        uploaded = _zip_file(
            "source.hwpx",
            {"Preview/PrvText.txt": "1. 세포막의 주성분은 무엇인가?\n① 단백질\n② 인지질\n정답 ②"},
        )

        source = extract_source(uploaded)

        self.assertEqual(source.kind, "HWPX")
        self.assertIn("세포막", source.extracted_text)
        self.assertIsNone(source.warning)

    def test_extracts_docx_document_text(self):
        uploaded = _zip_file(
            "source.docx",
            {
                "word/document.xml": (
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:body><w:p><w:r><w:t>1. 뉴클레오타이드의 구성 요소를 고르시오.</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>정답 3</w:t></w:r></w:p></w:body></w:document>"
                )
            },
        )

        source = extract_source(uploaded)

        self.assertEqual(source.kind, "DOCX")
        self.assertIn("뉴클레오타이드", source.extracted_text)

    def test_build_package_returns_endnote_ready_questions_without_ai(self):
        payload = {
            "variant_mode": "trap",
            "variant_count": 3,
            "note_policy": "교과서 개념 중심으로 짧게 설명합니다.",
            "use_ai": False,
            "text": "1. 아미노산의 종류는 몇 가지인가?\n① 4가지\n② 20가지\n정답 ②",
        }

        result = build_problem_studio_package(payload=payload, source_files=[])

        self.assertEqual(result["generation_engine"], "rule_fallback")
        self.assertEqual(result["mode"], "trap")
        self.assertGreaterEqual(len(result["questions"]), 1)
        self.assertIn("오답 유도", result["questions"][0]["explanation"])

    def test_transfer_only_preserves_original_block(self):
        payload = {
            "variant_mode": "copy",
            "variant_count": 1,
            "use_ai": False,
            "transfer_only": True,
            "text": "1. 광합성에서 생성되는 물질을 고르시오.\n① 산소와 포도당\n② 이산화탄소와 물\n정답 ①",
        }

        result = build_problem_studio_package(payload=payload, source_files=[])

        self.assertEqual(result["generation_engine"], "source_transfer")
        self.assertEqual(result["mode_label"], "원본 이관")
        self.assertIn("광합성에서 생성되는 물질", result["questions"][0]["prompt"])
        self.assertIn("정답 ①", result["questions"][0]["prompt"])
        self.assertEqual(result["questions"][0]["answer"], "①")
