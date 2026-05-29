"""
OMRWorkerCallback schema 검증 테스트.

worker contract 의 silent failure 차단이 작동하는지 확인.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from apps.domains.submissions.omr_pipeline.contracts.worker_response import (
    OMRPipelineStatus,
    SUPPORTED_WORKER_VERSIONS,
    parse_worker_callback,
)


class WorkerResponseSchemaTests(SimpleTestCase):
    def _valid_payload(self) -> dict:
        return {
            "submission_id": 123,
            "job_id": "abc-123",
            "status": "DONE",
            "kind": "omr_scan",
            "received_at": "2026-05-25T09:45:11.441386+00:00",
            "version": "v15.2",
            "result": {
                "aligned": True,
                "alignment_method": "marker_homography",
                "alignment_orientation": 0,
                "aligned_image_key": "tenants/2/ai/submissions/123/aligned/x.jpg",
                "aligned_image_size": {"width": 3508, "height": 2480},
                "answers": [
                    {
                        "question_id": 1,
                        "detected": ["1"],
                        "status": "ok",
                        "marking": "single",
                        "confidence": 0.92,
                    }
                ],
                "identifier": {
                    "identifier": "82378990",
                    "raw_identifier": "82378990",
                    "status": "ok",
                    "confidence": 0.65,
                    "digits": [],
                },
            },
        }

    def test_valid_payload_parses(self):
        cb, err = parse_worker_callback(self._valid_payload())
        self.assertIsNone(err)
        self.assertIsNotNone(cb)
        assert cb is not None  # type narrow
        self.assertEqual(cb.submission_id, 123)
        self.assertEqual(cb.status, OMRPipelineStatus.DONE)
        self.assertEqual(cb.version, "v15.2")
        self.assertIsNotNone(cb.result)
        assert cb.result is not None
        self.assertEqual(len(cb.result.answers), 1)
        self.assertEqual(cb.result.answers[0].detected, ["1"])
        self.assertIsNotNone(cb.result.identifier)
        assert cb.result.identifier is not None
        self.assertEqual(cb.result.identifier.identifier, "82378990")

    def test_unknown_version_rejected(self):
        payload = self._valid_payload()
        payload["version"] = "v99.never"
        cb, err = parse_worker_callback(payload)
        self.assertIsNone(cb)
        self.assertIsNotNone(err)
        assert err is not None
        self.assertIn("unsupported worker version", err)

    def test_missing_required_field_rejected(self):
        payload = self._valid_payload()
        del payload["submission_id"]
        cb, err = parse_worker_callback(payload)
        self.assertIsNone(cb)
        self.assertIsNotNone(err)

    def test_extra_envelope_field_rejected_after_phase_g(self):
        # Phase G: extra='forbid' 활성. parse_worker_callback 의 정규화 로직이
        # envelope 외 키를 result 본문으로 모으지 않는 단일 envelope-level 의
        # 미지 필드만 forbid 가 reject 한다 (예: 워커가 진짜 새 envelope 키 추가).
        # 정규화 자체를 우회하려면 model_validate 직접 호출.
        from apps.domains.submissions.omr_pipeline.contracts.worker_response import (
            OMRWorkerCallback,
        )
        from pydantic import ValidationError

        payload = self._valid_payload()
        payload["new_envelope_field"] = "surprise"
        with self.assertRaises(ValidationError):
            OMRWorkerCallback.model_validate(payload)

    def test_extra_non_envelope_top_level_goes_into_result_body(self):
        # parse_worker_callback 은 envelope 외 키를 result 본문으로 모은다.
        # 이건 forbid 와 무관 — schema 정규화가 먼저 일어남.
        payload = self._valid_payload()
        del payload["result"]
        payload["aligned"] = True  # legacy 형태: result 본문 키가 최상위
        cb, err = parse_worker_callback(payload)
        self.assertIsNone(err)
        self.assertIsNotNone(cb)

    def test_detected_normalized_from_dirty_input(self):
        payload = self._valid_payload()
        payload["result"]["answers"][0]["detected"] = [" 1 ", "", " ", "3"]
        cb, err = parse_worker_callback(payload)
        self.assertIsNone(err)
        assert cb is not None and cb.result is not None
        self.assertEqual(cb.result.answers[0].detected, ["1", "3"])

    def test_version_inferred_from_result_answers_if_top_level_missing(self):
        payload = self._valid_payload()
        del payload["version"]
        payload["result"]["answers"][0]["version"] = "v15"
        cb, err = parse_worker_callback(payload)
        self.assertIsNone(err)
        assert cb is not None
        self.assertEqual(cb.version, "v15")

    def test_non_dict_payload_rejected(self):
        cb, err = parse_worker_callback("not a dict")  # type: ignore[arg-type]
        self.assertIsNone(cb)
        self.assertIsNotNone(err)
        assert err is not None
        self.assertIn("not a dict", err)

    def test_invalid_status_value_rejected(self):
        payload = self._valid_payload()
        payload["status"] = "MOSTLY_DONE"
        cb, err = parse_worker_callback(payload)
        self.assertIsNone(cb)
        self.assertIsNotNone(err)

    def test_confidence_out_of_range_rejected(self):
        payload = self._valid_payload()
        payload["result"]["answers"][0]["confidence"] = 1.5
        cb, err = parse_worker_callback(payload)
        self.assertIsNone(cb)
        self.assertIsNotNone(err)

    def test_supported_versions_set_is_immutable(self):
        # 운영 사고 방지: 정의된 set 이 frozenset 인지 확인
        self.assertIsInstance(SUPPORTED_WORKER_VERSIONS, frozenset)
        self.assertIn("v15.2", SUPPORTED_WORKER_VERSIONS)
