from __future__ import annotations

import base64
import json
import zipfile
import zlib
from io import BytesIO
from unittest.mock import patch

from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

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
    TransferOcrContext,
)
from apps.domains.tools.problem_studio.extractors import extract_hwpx_text
from apps.domains.tools.problem_studio.ocr import OcrResult
from apps.domains.tools.problem_studio.async_transfer import (
    SOURCE_ARCHIVE_MANIFEST,
    build_source_archive,
    source_files_from_archive,
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
    def test_ai_ocr_context_injects_transcribed_text_into_hwpx(self):
        calls: list[tuple[int, str]] = []

        def transcribe(data: bytes, mime: str) -> OcrResult:
            calls.append((len(data), mime))
            return OcrResult(
                text="1. 다음 중 산화 환원 반응을 고르시오.\n① 반응 A\n정답 ①",
                status="extracted",
                engine="openai:test-model",
            )

        package = build_transfer_package(
            payload={"title": "AI 타이핑"},
            source_files=[SimpleUploadedFile("scan.png", _TINY_PNG)],
            ocr_context=TransferOcrContext(max_units=1, extractor=transcribe),
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "image/png")
        self.assertEqual(package.structured_item_count, 1)
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            preview = zf.read("03_자체양식_문제검수본.hwpx")
        self.assertIn("산화 환원 반응", extract_hwpx_text(preview))

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
        self.assertIn("02_OCR_연결후보.csv", names)
        self.assertIn("03_자체양식_문제검수본.hwpx", names)

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

        self.assertEqual(package.review_file_count, 7)
        self.assertEqual(len(package.documents), 1)
        self.assertEqual(len(package.warnings), 1)
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            names = zf.namelist()
            checklist = zf.read("00_먼저열기_검수체크리스트.doc").decode("utf-8-sig")
            manifest = json.loads(zf.read("00_manifest.json").decode("utf-8"))
            csv_text = zf.read("00_파일목록.csv").decode("utf-8-sig")

        self.assertIn("실사용 검수 순서", checklist)
        self.assertIn("01_자체양식_문제검수본.doc", checklist)
        self.assertIn("02_OCR_연결후보.csv", checklist)
        self.assertIn("03_자체양식_문제검수본.hwpx", checklist)
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
        self.assertTrue(manifest["review_contract"]["native_hwpx_output"])
        self.assertFalse(manifest["review_contract"]["native_hwp_output"])
        self.assertEqual(manifest["documents"][0]["status"], "확인 필요")
        self.assertEqual(manifest["template_outputs"][0]["filename"], "01_자체양식_문제검수본.doc")
        self.assertEqual(manifest["template_outputs"][1]["filename"], "03_자체양식_문제검수본.hwpx")
        self.assertEqual(manifest["template_outputs"][2]["filename"], "02_OCR_연결후보.csv")
        self.assertIn("산출물", csv_text)
        self.assertIn("검수본", csv_text)
        self.assertIn("legacy_원본이관.doc", csv_text)
        self.assertIn("00_manifest.json", names)
        self.assertIn("01_자체양식_문제검수본.doc", names)
        self.assertIn("02_OCR_연결후보.csv", names)
        self.assertIn("03_자체양식_문제검수본.hwpx", names)

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

        self.assertEqual(package.review_file_count, 7)
        self.assertEqual(package.structured_item_count, 1)
        self.assertEqual(package.ocr_candidate_count, 0)
        self.assertEqual(response["X-Problem-Studio-Structured-Item-Count"], "1")
        self.assertEqual(response["X-Problem-Studio-OCR-Candidate-Count"], "0")
        self.assertEqual(response["X-Problem-Studio-Quality-Level"], "structured_review_ready")
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            workbook = zf.read("01_자체양식_문제검수본.doc").decode("utf-8-sig")
            hwpx_data = zf.read("03_자체양식_문제검수본.hwpx")
            manifest = json.loads(zf.read("00_manifest.json").decode("utf-8"))

        self.assertIn("자체양식 문제검수본", workbook)
        self.assertIn("물의 자동 이온화 상수", workbook)
        self.assertIn("물의 자동 이온화 상수", extract_hwpx_text(hwpx_data))
        self.assertIn("정답", workbook)
        self.assertEqual(manifest["structured_item_count"], 1)
        self.assertEqual(manifest["structured_problem_count"], 1)
        self.assertEqual(manifest["ocr_candidate_count"], 0)

    def test_transfer_package_writes_ocr_queue_for_image_only_source(self):
        uploaded = SimpleUploadedFile("scan.png", _TINY_PNG)

        package = build_transfer_package(payload={"title": "스캔 검수"}, source_files=[uploaded])

        self.assertEqual(package.review_file_count, 7)
        self.assertEqual(package.ocr_candidate_count, 1)
        self.assertEqual(package.quality_level, "visual_only_ocr_required")
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            manifest = json.loads(zf.read("00_manifest.json").decode("utf-8"))
            ocr_csv = zf.read("02_OCR_연결후보.csv").decode("utf-8-sig")
            workbook = zf.read("01_자체양식_문제검수본.doc").decode("utf-8-sig")

        candidate = manifest["structure"]["ocr_candidates"][0]
        self.assertEqual(candidate["candidate_id"], "ocr-001")
        self.assertEqual(candidate["priority"], "high")
        self.assertIn("scan.png", ocr_csv)
        self.assertIn("ocr-001", ocr_csv)
        self.assertIn("자동 OCR 및 연결 후보", workbook)

    def test_transfer_package_includes_hwpx_review_workbook_package(self):
        uploaded = _zip_file(
            "chemistry.docx",
            {
                "word/document.xml": (
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:body>"
                    "<w:p><w:r><w:t>1. 산화수 보존을 확인하시오.</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>정답 1</w:t></w:r></w:p>"
                    "</w:body></w:document>"
                )
            },
        )

        package = build_transfer_package(payload={"title": "HWPX 검수"}, source_files=[uploaded])

        with zipfile.ZipFile(BytesIO(package.data)) as outer:
            hwpx_data = outer.read("03_자체양식_문제검수본.hwpx")
        with zipfile.ZipFile(BytesIO(hwpx_data)) as inner:
            first = inner.infolist()[0]
            names = inner.namelist()
            preview = inner.read("Preview/PrvText.txt").decode("utf-8")
            section = inner.read("Contents/section0.xml").decode("utf-8")

        self.assertEqual(first.filename, "mimetype")
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
        self.assertIn("Contents/content.hpf", names)
        self.assertIn("Contents/header.xml", names)
        self.assertIn("산화수 보존", preview)
        self.assertIn("<hp:t>", section)
        self.assertIn("산화수 보존", extract_hwpx_text(hwpx_data))

    def test_transfer_package_uses_successful_ocr_text_for_image_source(self):
        uploaded = SimpleUploadedFile("scan.png", _TINY_PNG)

        with patch(
            "apps.domains.tools.problem_studio.transfer_documents.extract_ocr_text_from_image",
            return_value=OcrResult(
                text="1. 산과 염기의 중화 반응을 고르시오.\n① 중화\n정답 ①",
                status="extracted",
                engine="tesseract:kor+eng",
            ),
        ):
            package = build_transfer_package(payload={"title": "자동 OCR"}, source_files=[uploaded])

        self.assertEqual(package.ocr_candidate_count, 0)
        self.assertEqual(package.structured_item_count, 1)
        self.assertEqual(package.quality_level, "structured_review_ready")
        self.assertEqual(package.documents[0].ocr_completed_units, 1)
        self.assertEqual(package.documents[0].ocr_pending_units, 0)
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            manifest = json.loads(zf.read("00_manifest.json").decode("utf-8"))
            workbook = zf.read("01_자체양식_문제검수본.doc").decode("utf-8-sig")
            ocr_csv = zf.read("02_OCR_연결후보.csv").decode("utf-8-sig")

        self.assertEqual(manifest["ocr_completed_unit_count"], 1)
        self.assertEqual(manifest["ocr_pending_unit_count"], 0)
        self.assertFalse(manifest["review_contract"]["ocr_required_for_scanned_text"])
        self.assertIn("중화 반응", workbook)
        self.assertIn("남은 OCR 후보 없음", ocr_csv)

    def test_async_transfer_archive_round_trips_uploaded_sources(self):
        uploaded = _zip_file(
            "chemistry.docx",
            {
                "word/document.xml": (
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:body>"
                    "<w:p><w:r><w:t>1. 암모니아 합성 평형 조건은?</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>정답 ②</w:t></w:r></w:p>"
                    "</w:body></w:document>"
                )
            },
        )

        archive_file, manifest_files = build_source_archive([uploaded])
        try:
            self.assertEqual(manifest_files[0]["name"], "chemistry.docx")
            with zipfile.ZipFile(archive_file) as zf:
                names = zf.namelist()
                self.assertIn(SOURCE_ARCHIVE_MANIFEST, names)
            archive_file.seek(0)
            with source_files_from_archive(archive_file) as source_files:
                package = build_transfer_package(payload={"title": "비동기 이관"}, source_files=source_files)
        finally:
            archive_file.close()

        self.assertEqual(package.structured_item_count, 1)
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            workbook = zf.read("01_자체양식_문제검수본.doc").decode("utf-8-sig")
        self.assertIn("암모니아 합성 평형", workbook)

    def test_async_transfer_archive_rejects_manifest_size_tampering(self):
        archive_file, _ = build_source_archive([SimpleUploadedFile("scan.png", _TINY_PNG)])
        try:
            with zipfile.ZipFile(archive_file) as source_zip:
                manifest = json.loads(source_zip.read(SOURCE_ARCHIVE_MANIFEST).decode("utf-8"))
                manifest["files"][0]["size"] += 1
                tampered = BytesIO()
                with zipfile.ZipFile(tampered, "w") as target_zip:
                    for info in source_zip.infolist():
                        content = source_zip.read(info.filename)
                        if info.filename == SOURCE_ARCHIVE_MANIFEST:
                            content = json.dumps(manifest).encode("utf-8")
                        target_zip.writestr(info, content)
                tampered.seek(0)
        finally:
            archive_file.close()

        with self.assertRaisesRegex(ValueError, "manifest와 다릅니다"):
            with source_files_from_archive(tampered):
                pass

    def test_transfer_worker_rejects_payload_tenant_mismatch(self):
        from academy.application.use_cases.ai.pipelines.problem_studio_transfer_handler import (
            handle_problem_studio_transfer_job,
        )

        job = AIJob.new(
            type="problem_studio_transcription",
            tenant_id="7",
            source_domain="tools_problem_studio",
            payload={
                "tenant_id": "8",
                "source_archive_key": "tenants/8/tools/problem-studio/tmp/test/sources.zip",
            },
        )

        result = handle_problem_studio_transfer_job(job)

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error, "tenant_id mismatch")

    def test_transfer_package_reports_zip_with_too_many_members(self):
        uploaded = SimpleUploadedFile(
            "many.zip",
            _zip_bytes({f"{index}.png": _TINY_PNG for index in range(TRANSFER_MAX_ZIP_MEMBERS + 1)}),
        )

        package = build_transfer_package(payload={"title": "파괴 테스트"}, source_files=[uploaded])

        self.assertEqual(len(package.documents), 1)
        self.assertEqual(len(package.warnings), 1)
        self.assertIn("ZIP 해제 중 오류", package.warnings[0])

    def test_transfer_package_reports_structure_limit_in_review_document(self):
        paragraphs = "".join(
            f"<w:p><w:r><w:t>1. 반응 {index}을 고르시오.</w:t></w:r></w:p>"
            for index in range(1, 82)
        )
        uploaded = _zip_file(
            "many-questions.docx",
            {
                "word/document.xml": (
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    f"<w:body>{paragraphs}</w:body></w:document>"
                )
            },
        )

        package = build_transfer_package(payload={"title": "구조화 한도"}, source_files=[uploaded])

        self.assertTrue(package.structure_limit_reached)
        self.assertEqual(package.structured_item_count, 80)
        with zipfile.ZipFile(BytesIO(package.data)) as zf:
            hwpx = zf.read("03_자체양식_문제검수본.hwpx")
        self.assertIn("최대 80개", extract_hwpx_text(hwpx))

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


class ProblemStudioTransferViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.core.cache import cache
        from apps.core.models import Tenant, TenantMembership

        cache.clear()
        self.tenant = Tenant.objects.create(name="Problem Studio", code="problem_studio", is_active=True)
        self.user = get_user_model().objects.create_user(
            username="problem_studio_owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")

    def _request(self, method: str, path: str):
        from rest_framework.test import APIRequestFactory, force_authenticate

        factory = APIRequestFactory()
        request = getattr(factory, method)(path)
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return request

    @patch(
        "academy.adapters.storage.r2_objects.create_storage_download_url",
        return_value="https://download.example/Academy-Hangul-Companion-Windows-1.0.0.zip",
    )
    @patch(
        "academy.adapters.storage.r2_objects.head_storage_object_integrity",
        return_value=(67644035, "83ed43fed33a4eedb8aa92321bf672de9ce135a3429325d978ae96559b0fdda6"),
    )
    def test_hangul_companion_download_is_staff_only_and_manifest_bound(self, mock_head, mock_presign):
        from apps.domains.tools.problem_studio.views import ProblemStudioHangulCompanionDownloadView

        response = ProblemStudioHangulCompanionDownloadView.as_view()(
            self._request("get", "/api/v1/tools/problem-studio/hangul-companion/"),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["version"], "1.0.0")
        self.assertEqual(response.data["size_bytes"], 67644035)
        self.assertEqual(response.data["sha256"], "83ed43fed33a4eedb8aa92321bf672de9ce135a3429325d978ae96559b0fdda6")
        self.assertNotIn("r2_key", response.data)
        self.assertEqual(response["Cache-Control"], "no-store")
        mock_head.assert_called_once()
        mock_presign.assert_called_once()

    @patch("academy.adapters.storage.r2_objects.create_storage_download_url")
    @patch("academy.adapters.storage.r2_objects.head_storage_object_integrity", return_value=(67644035, "b" * 64))
    def test_hangul_companion_download_fails_closed_on_object_mismatch(self, _mock_head, mock_presign):
        from apps.domains.tools.problem_studio.views import ProblemStudioHangulCompanionDownloadView

        response = ProblemStudioHangulCompanionDownloadView.as_view()(
            self._request("get", "/api/v1/tools/problem-studio/hangul-companion/"),
        )

        self.assertEqual(response.status_code, 503, response.data)
        mock_presign.assert_not_called()

    def test_hangul_companion_download_rejects_student_membership(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership
        from apps.domains.tools.problem_studio.views import ProblemStudioHangulCompanionDownloadView

        student = get_user_model().objects.create_user(
            username="problem_studio_student",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=student, role="student")
        request = APIRequestFactory().get("/api/v1/tools/problem-studio/hangul-companion/")
        request.tenant = self.tenant
        force_authenticate(request, user=student)

        response = ProblemStudioHangulCompanionDownloadView.as_view()(request)

        self.assertEqual(response.status_code, 403, response.data)

    @patch("academy.adapters.storage.r2_objects.create_storage_download_url")
    @patch(
        "academy.adapters.storage.r2_objects.head_storage_object_integrity",
        side_effect=RuntimeError("R2 unavailable"),
    )
    def test_hangul_companion_download_fails_closed_when_r2_is_unavailable(self, _mock_head, mock_presign):
        from apps.domains.tools.problem_studio.views import ProblemStudioHangulCompanionDownloadView

        response = ProblemStudioHangulCompanionDownloadView.as_view()(
            self._request("get", "/api/v1/tools/problem-studio/hangul-companion/"),
        )

        self.assertEqual(response.status_code, 503, response.data)
        mock_presign.assert_not_called()

    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage", return_value="https://download.example/review.zip")
    def test_transfer_status_reissues_url_without_exposing_r2_key(self, mock_presign):
        from apps.domains.tools.problem_studio.views import ProblemStudioTransferJobStatusView

        AIJobModel = django_apps.get_model("ai_domain", "AIJobModel")
        AIResultModel = django_apps.get_model("ai_domain", "AIResultModel")
        job = AIJobModel.objects.create(
            job_id="problem-status-job",
            job_type="problem_studio_transcription",
            status="DONE",
            tenant_id=str(self.tenant.id),
            source_domain="tools_problem_studio",
            tier="basic",
        )
        AIResultModel.objects.create(job=job, payload={
            "r2_key": f"tenants/{self.tenant.id}/tools/problem-studio/result/review.zip",
            "download_url": "https://expired.example/review.zip",
            "filename": "검수본.zip",
            "size_bytes": 12,
        })

        response = ProblemStudioTransferJobStatusView.as_view()(
            self._request("get", f"/api/v1/tools/problem-studio/transfer-jobs/{job.job_id}/"),
            job_id=job.job_id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["result"]["download_url"], "https://download.example/review.zip")
        self.assertNotIn("r2_key", response.data["result"])
        mock_presign.assert_called_once()

    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage", return_value="https://download.example/review.zip")
    def test_hangul_handoff_is_one_time(self, mock_presign):
        from urllib.parse import parse_qs, unquote, urlparse
        from apps.domains.tools.problem_studio.views import (
            ProblemStudioHangulHandoffConsumeView,
            ProblemStudioHangulHandoffCreateView,
        )

        AIJobModel = django_apps.get_model("ai_domain", "AIJobModel")
        AIResultModel = django_apps.get_model("ai_domain", "AIResultModel")
        job = AIJobModel.objects.create(
            job_id="problem-handoff-job",
            job_type="problem_studio_transcription",
            status="DONE",
            tenant_id=str(self.tenant.id),
            source_domain="tools_problem_studio",
            tier="basic",
        )
        AIResultModel.objects.create(job=job, payload={
            "r2_key": f"tenants/{self.tenant.id}/tools/problem-studio/result/review.zip",
            "filename": "검수본.zip",
            "size_bytes": 12,
            "sha256": "a" * 64,
        })

        create_response = ProblemStudioHangulHandoffCreateView.as_view()(
            self._request("post", f"/api/v1/tools/problem-studio/transfer-jobs/{job.job_id}/hangul-handoff/"),
            job_id=job.job_id,
        )
        self.assertEqual(create_response.status_code, 200, create_response.data)
        protocol = urlparse(create_response.data["protocol_url"])
        handoff_url = unquote(parse_qs(protocol.query)["handoff"][0])
        token = urlparse(handoff_url).path.rstrip("/").rsplit("/", 1)[-1]

        consume_view = ProblemStudioHangulHandoffConsumeView.as_view()
        first = consume_view(self._request("get", f"/api/v1/tools/problem-studio/hangul-handoffs/{token}/"), token=token)
        second = consume_view(self._request("get", f"/api/v1/tools/problem-studio/hangul-handoffs/{token}/"), token=token)

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["sha256"], "a" * 64)
        self.assertEqual(second.status_code, 404, second.data)
        mock_presign.assert_called_once()
