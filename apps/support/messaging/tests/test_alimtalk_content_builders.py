from unittest import TestCase

from apps.support.messaging.alimtalk_content_builders import (
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
    """커뮤니티 답변 알림톡 트리거 — TYPE_SCORE 통합 템플릿 재사용."""

    def test_qna_answered_maps_to_score(self):
        self.assertEqual(get_template_type("qna_answered"), TYPE_SCORE)
        self.assertEqual(get_solapi_template_id("qna_answered"), SOLAPI_SCORE)

    def test_counsel_answered_maps_to_score(self):
        self.assertEqual(get_template_type("counsel_answered"), TYPE_SCORE)
        self.assertEqual(get_solapi_template_id("counsel_answered"), SOLAPI_SCORE)

    def test_qna_answered_replacements_shape_and_truncate(self):
        long_title = "함수의 미분 질문 입니다 매우 길어서 23자를 초과합니다 절대로"
        replacements = build_unified_replacements(
            trigger="qna_answered",
            content_body="선생님이 질문에 답변하셨습니다.",
            context={"강의명": "수학", "차시명": long_title},
            tenant_name="학원플러스",
            student_name="홍길동",
            site_url="https://hakwonplus.com",
        )
        keys = [r["key"] for r in replacements]
        self.assertEqual(
            keys,
            ["학원이름", "학생이름", "강의명", "차시명", "선생님메모", "사이트링크"],
        )
        by_key = {r["key"]: r["value"] for r in replacements}
        self.assertEqual(by_key["학원이름"], "학원플러스")
        self.assertEqual(by_key["학생이름"], "홍길동")
        self.assertEqual(by_key["강의명"], "수학")
        self.assertLessEqual(len(by_key["차시명"]), 23)
        self.assertTrue(by_key["차시명"].endswith("…"))
        self.assertEqual(by_key["사이트링크"], "https://hakwonplus.com")
        self.assertIn("선생님이 질문에 답변", by_key["선생님메모"])

    def test_counsel_answered_falls_back_to_default_category(self):
        replacements = build_unified_replacements(
            trigger="counsel_answered",
            content_body="신청하신 상담에 답변이 등록되었습니다.",
            context={"강의명": "진로 상담", "차시명": "고3 진학 상담"},
            tenant_name="학원플러스",
            student_name="홍길동",
            site_url="https://hakwonplus.com",
        )
        by_key = {r["key"]: r["value"] for r in replacements}
        self.assertEqual(by_key["강의명"], "진로 상담")
        self.assertEqual(by_key["차시명"], "고3 진학 상담")
