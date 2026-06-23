from __future__ import annotations

import base64
import json
import zipfile
import zlib
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.shared.contracts.ai_job import AIJob
from apps.domains.tools.problem_studio.services import (
    build_problem_studio_package,
    build_problem_studio_package_from_worker_payload,
    extract_source,
    parse_payload,
    source_extraction_to_payload,
)
from apps.domains.tools.problem_studio.worker import handle_problem_studio_package_job
from apps.domains.tools.problem_studio.transfer_documents import (
    TRANSFER_MAX_ZIP_MEMBERS,
    build_transfer_package,
    package_to_response,
    _normalize_hwp_image_data,
)


def _zip_file(name: str, files: dict[str, str]) -> SimpleUploadedFile:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return SimpleUploadedFile(name, buf.getvalue())


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


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

    def test_extracts_zip_nested_docx_text_for_beta_rewrite(self):
        uploaded = _zip_file(
            "sources.zip",
            {
                "unit1/source.docx": _zip_bytes({
                    "word/document.xml": (
                        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                        "<w:body><w:p><w:r><w:t>암모니아 합성 평형 이동 조건을 고르시오.</w:t></w:r></w:p>"
                        "<w:p><w:r><w:t>정답 ②</w:t></w:r></w:p></w:body></w:document>"
                    ).encode()
                }),
                "ignore.txt": b"skip",
            },
        )

        source = extract_source(uploaded)

        self.assertEqual(source.kind, "ZIP")
        self.assertIn("암모니아 합성", source.extracted_text)
        self.assertIsNone(source.warning)

    def test_beta_zip_text_extraction_reports_too_many_members(self):
        uploaded = SimpleUploadedFile(
            "too-many.zip",
            _zip_bytes({f"{index}.docx": b"not-a-docx" for index in range(401)}),
        )

        source = extract_source(uploaded)

        self.assertEqual(source.kind, "ZIP")
        self.assertEqual(source.extracted_text, "")
        self.assertIn("파일 수가 너무 많습니다", source.warning or "")

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

    def test_worker_payload_rebuilds_package_from_extracted_sources(self):
        source = extract_source(_zip_file(
            "source.hwpx",
            {"Preview/PrvText.txt": "1. 광합성에서 필요한 기체는?\n① 산소\n② 이산화탄소\n정답 ②"},
        ))
        payload = {
            "problem_studio_payload": {
                "variant_mode": "copy",
                "variant_count": 1,
                "use_ai": False,
                "transfer_only": True,
            },
            "source_files": [source_extraction_to_payload(source)],
        }

        result = build_problem_studio_package_from_worker_payload(payload)

        self.assertEqual(result["generation_engine"], "source_transfer")
        self.assertEqual(result["source_files"][0]["name"], "source.hwpx")
        self.assertIn("광합성에서 필요한 기체", result["questions"][0]["prompt"])

    def test_worker_handler_returns_ai_result_done(self):
        job = AIJob.new(
            type="problem_studio_package",
            tenant_id="1",
            source_domain="tools_problem_studio",
            payload={
                "problem_studio_payload": {
                    "variant_mode": "copy",
                    "use_ai": False,
                    "transfer_only": True,
                    "text": "1. 뉴클레오타이드 염기의 종류는?\n정답 4",
                },
                "source_files": [],
            },
        )

        result = handle_problem_studio_package_job(job)

        self.assertEqual(result.status, "DONE")
        self.assertEqual(result.result["generation_engine"], "source_transfer")
        self.assertIn("뉴클레오타이드", result.result["questions"][0]["prompt"])

    def test_transfer_package_expands_zip_and_embeds_image_doc(self):
        uploaded = SimpleUploadedFile(
            "sources.zip",
            _zip_bytes({"unit1/problem.png": _TINY_PNG, "ignored.txt": b"skip"}),
        )

        package = build_transfer_package(
            payload={"title": "화학2 1단원"},
            source_files=[uploaded],
        )

        self.assertEqual(package.content_type, "application/zip")
        self.assertEqual(len(package.documents), 1)
        self.assertEqual(package.documents[0].image_count, 1)
        self.assertIn("data:image/png;base64", package.documents[0].html)
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            names = zf.namelist()
        self.assertTrue(any(name.endswith(".doc") for name in names))
        self.assertIn("00_먼저열기_검수체크리스트.doc", names)
        self.assertIn("00_변환리포트.html", names)
        self.assertIn("00_manifest.json", names)
        self.assertIn("00_파일목록.csv", names)

    def test_transfer_package_includes_review_manifest_and_warning_actions(self):
        uploaded = SimpleUploadedFile("legacy.doc", b"legacy-binary")

        package = build_transfer_package(
            payload={
                "title": "화학2 1단원",
                "class_name": "고3",
                "subject": "화학II",
                "template_name": "신민 샘플",
            },
            source_files=[uploaded],
        )

        self.assertEqual(package.review_file_count, 5)
        self.assertEqual(len(package.documents), 1)
        self.assertEqual(len(package.warnings), 1)
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            names = zf.namelist()
            checklist = zf.read("00_먼저열기_검수체크리스트.doc").decode("utf-8-sig")
            manifest = json.loads(zf.read("00_manifest.json").decode("utf-8"))
            csv_text = zf.read("00_파일목록.csv").decode("utf-8-sig")

        self.assertIn("실사용 검수 순서", checklist)
        self.assertIn("01_자체양식_문제검수본.doc", checklist)
        self.assertIn("legacy.doc", checklist)
        self.assertIn("DOCX/PDF로 저장", checklist)
        self.assertEqual(manifest["schema"], "problem-studio-transfer-manifest/v2")
        self.assertEqual(manifest["title"], "화학2 1단원")
        self.assertEqual(manifest["class_name"], "고3")
        self.assertEqual(manifest["document_count"], 1)
        self.assertEqual(manifest["warning_count"], 1)
        self.assertEqual(manifest["structured_item_count"], 0)
        self.assertEqual(manifest["quality_level"], "needs_attention")
        self.assertTrue(manifest["review_contract"]["teacher_must_verify_answers"])
        self.assertEqual(manifest["documents"][0]["status"], "확인 필요")
        self.assertEqual(manifest["template_outputs"][0]["filename"], "01_자체양식_문제검수본.doc")
        self.assertIn("산출물", csv_text)
        self.assertIn("검수본", csv_text)
        self.assertIn("legacy_원본이관.doc", csv_text)
        self.assertIn("00_manifest.json", names)
        self.assertIn("01_자체양식_문제검수본.doc", names)

    def test_transfer_package_builds_structured_workbook_from_docx_text(self):
        uploaded = _zip_file(
            "chemistry.docx",
            {
                "word/document.xml": (
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:body>"
                    "<w:p><w:r><w:t>1. 물의 자동 이온화 상수를 고르시오.</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>① Kw ② Ka ③ Kb</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>정답 ①</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>해설 물의 자동 이온화 평형을 나타냅니다.</w:t></w:r></w:p>"
                    "</w:body></w:document>"
                )
            },
        )

        package = build_transfer_package(payload={"title": "화학2 구조화"}, source_files=[uploaded])
        response = package_to_response(package)

        self.assertEqual(package.review_file_count, 5)
        self.assertEqual(package.structured_item_count, 1)
        self.assertEqual(package.ocr_candidate_count, 0)
        self.assertEqual(response["X-Problem-Studio-Structured-Item-Count"], "1")
        self.assertEqual(response["X-Problem-Studio-OCR-Candidate-Count"], "0")
        self.assertEqual(response["X-Problem-Studio-Quality-Level"], "structured_review_ready")
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            workbook = zf.read("01_자체양식_문제검수본.doc").decode("utf-8-sig")
            manifest = json.loads(zf.read("00_manifest.json").decode("utf-8"))

        self.assertIn("자체양식 문제검수본", workbook)
        self.assertIn("물의 자동 이온화 상수", workbook)
        self.assertIn("정답", workbook)
        self.assertEqual(manifest["structured_item_count"], 1)
        self.assertEqual(manifest["structured_problem_count"], 1)
        self.assertEqual(manifest["ocr_candidate_count"], 0)

    def test_transfer_package_reports_zip_with_too_many_members(self):
        uploaded = SimpleUploadedFile(
            "many.zip",
            _zip_bytes({f"{index}.png": _TINY_PNG for index in range(TRANSFER_MAX_ZIP_MEMBERS + 1)}),
        )

        package = build_transfer_package(payload={"title": "파괴 테스트"}, source_files=[uploaded])

        self.assertEqual(len(package.documents), 1)
        self.assertEqual(len(package.warnings), 1)
        self.assertIn("ZIP 해제 중 오류", package.warnings[0])

    def test_parse_payload_rejects_broken_json(self):
        with self.assertRaises(ValueError):
            parse_payload("{not-json")

    def test_hwp_image_normalization_inflates_compressed_bindata(self):
        from PIL import Image

        bmp_buf = BytesIO()
        Image.new("RGB", (2, 2), "white").save(bmp_buf, format="BMP")
        compressor = zlib.compressobj(wbits=-15)
        compressed_bmp = compressor.compress(bmp_buf.getvalue()) + compressor.flush()

        mime, data = _normalize_hwp_image_data("BIN0001.bmp", compressed_bmp)

        self.assertEqual(mime, "image/png")
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.size, (2, 2))

        jpg_buf = BytesIO()
        Image.new("RGB", (2, 2), "black").save(jpg_buf, format="JPEG")
        compressor = zlib.compressobj(wbits=-15)
        compressed_jpg = compressor.compress(jpg_buf.getvalue()) + compressor.flush()

        mime, data = _normalize_hwp_image_data("BIN0002.jpg", compressed_jpg)

        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))
