from unittest import TestCase

from apps.support.messaging.alimtalk_content_builders import get_unified_for_category


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
