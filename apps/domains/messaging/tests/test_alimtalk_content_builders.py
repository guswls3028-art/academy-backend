from unittest import TestCase

from apps.domains.messaging.alimtalk_content_builders import (
    build_unified_replacements,
    get_solapi_template_id,
    get_template_type,
    get_unified_for_category,
    SOLAPI_SCORE,
    TYPE_SCORE,
)


class TestUnifiedCategoryClinicRouting(TestCase):
    def test_clinic_change_korean_name(self):
        tt, sid = get_unified_for_category("clinic", "클리닉 일정 변경 안내", {})
        self.assertEqual(tt, "clinic_change")
        self.assertTrue(bool(sid))

    def test_clinic_change_english_name(self):
        english_cases = [
            "clinic change notice",
            "clinic changed",
            "clinic cancel",
            "clinic cancelled",
            "clinic canceled",
            "clinic reschedule",
            "clinic rescheduled",
        ]
        for name in english_cases:
            with self.subTest(name=name):
                tt, sid = get_unified_for_category("clinic", name, {})
                self.assertEqual(tt, "clinic_change")
                self.assertTrue(bool(sid))

    def test_clinic_change_mixed_name(self):
        tt, sid = get_unified_for_category("clinic", "클리닉 rescheduled 안내", {})
        self.assertEqual(tt, "clinic_change")
        self.assertTrue(bool(sid))

    def test_clinic_change_from_extra_vars(self):
        tt, sid = get_unified_for_category(
            "clinic",
            "클리닉 안내",
            {"클리닉변동사항": "시간 변경"},
        )
        self.assertEqual(tt, "clinic_change")
        self.assertTrue(bool(sid))


class TestCommunityTriggers(TestCase):
    """커뮤니티 답변 알림톡 트리거 — 매핑 부재 시 통합 알림톡 미사용 (옛 score 좀비 fallback 종료)."""

    def test_qna_answered_no_unified_mapping(self):
        # 카카오 검수 통과된 적합 양식이 없음 → 통합 알림톡 비활성
        self.assertIsNone(get_template_type("qna_answered"))
        self.assertIsNone(get_solapi_template_id("qna_answered"))

    def test_counsel_answered_no_unified_mapping(self):
        self.assertIsNone(get_template_type("counsel_answered"))
        self.assertIsNone(get_solapi_template_id("counsel_answered"))

    def test_unmapped_trigger_returns_empty_replacements(self):
        replacements = build_unified_replacements(
            trigger="qna_answered",
            content_body="선생님이 질문에 답변하셨습니다.",
            context={"강의명": "수학"},
            tenant_name="학원플러스",
            student_name="홍길동",
            site_url="https://hakwonplus.com",
        )
        self.assertEqual(replacements, [])
