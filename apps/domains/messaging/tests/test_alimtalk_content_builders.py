from unittest import TestCase

from apps.domains.messaging.alimtalk_content_builders import (
    build_manual_replacements,
    build_unified_replacements,
    get_solapi_template_id,
    get_template_type,
    get_unified_for_category,
    TYPE_ATTENDANCE,
    TYPE_CLINIC_INFO,
    TYPE_NOTICE_PAYMENT,
    TYPE_NOTICE_WITHDRAWAL,
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


class TestSystemNoticeMappings(TestCase):
    """퇴원/결제 NONE 양식은 고정 본문 시스템 안내로 계속 라우팅한다."""

    def test_withdrawal_complete_uses_withdrawal_notice(self):
        self.assertEqual(
            get_template_type("withdrawal_complete"),
            TYPE_NOTICE_WITHDRAWAL,
        )
        self.assertTrue(bool(get_solapi_template_id("withdrawal_complete")))

    def test_payment_triggers_use_payment_notice(self):
        for trigger in ("payment_complete", "payment_due_days_before"):
            with self.subTest(trigger=trigger):
                self.assertEqual(get_template_type(trigger), TYPE_NOTICE_PAYMENT)
                self.assertFalse(bool(get_solapi_template_id(trigger)))

    def test_payment_category_uses_payment_notice(self):
        tt, sid = get_unified_for_category("payment")
        self.assertEqual(tt, TYPE_NOTICE_PAYMENT)
        self.assertFalse(bool(sid))


class TestExamAssignmentEnvelopeMappings(TestCase):
    """시험/과제 안내는 자동·수동 모두 출석 안내 ITEM_LIST 봉투를 재사용한다."""

    def test_exam_and_assignment_triggers_use_attendance_envelope(self):
        for trigger in (
            "exam_scheduled_days_before",
            "exam_start_minutes_before",
            "exam_not_taken",
            "assignment_registered",
            "assignment_due_hours_before",
            "assignment_not_submitted",
        ):
            with self.subTest(trigger=trigger):
                self.assertEqual(get_template_type(trigger), TYPE_ATTENDANCE)
                self.assertTrue(bool(get_solapi_template_id(trigger)))

    def test_exam_and_assignment_categories_use_attendance_envelope(self):
        for category in ("exam", "assignment"):
            with self.subTest(category=category):
                tt, sid = get_unified_for_category(category)
                self.assertEqual(tt, TYPE_ATTENDANCE)
                self.assertTrue(bool(sid))

    def test_retake_trigger_uses_clinic_info_envelope(self):
        self.assertEqual(get_template_type("retake_assigned"), TYPE_CLINIC_INFO)
        self.assertTrue(bool(get_solapi_template_id("retake_assigned")))


class TestRegisteredSolapiVariables(TestCase):
    def test_manual_replacements_use_registered_teacher_memo_only(self):
        replacements = build_manual_replacements(
            template_type=TYPE_CLINIC_INFO,
            content_body="내일 클리닉 안내입니다.",
            context={"장소": "301호", "날짜": "2026-05-26", "시간": "18:30"},
            tenant_name="림글리쉬",
            student_name="홍길동",
            site_url="https://limglish.hakwonplus.com",
        )
        reps = {item["key"]: item["value"] for item in replacements}
        self.assertEqual(reps["선생님메모"], "내일 클리닉 안내입니다.")
        self.assertNotIn("선생님메모1", reps)

    def test_score_replacements_match_registered_solapi_variables(self):
        replacements = build_manual_replacements(
            template_type=TYPE_SCORE,
            content_body="#{학생이름} 성적 안내입니다.\n#{시험1명}: #{시험1}/#{시험1만점}",
            context={
                "강의명": "수학A반",
                "차시명": "3회차",
                "날짜": "7월 8일",
                "시험1명": "단원평가",
                "시험1": "92",
                "시험1만점": "100",
                "시험총점": "92",
                "시험총만점": "100",
                "숙제완성도": "1/1 완료",
            },
            tenant_name="림글리쉬",
            student_name="홍길동",
            site_url="https://limglish.hakwonplus.com",
        )
        keys = [item["key"] for item in replacements]
        self.assertEqual(
            keys,
            [
                "학원이름", "학생이름", "학생이름3", "강의명", "차시명", "날짜",
                "시험1명", "시험1", "시험1만점",
                "시험2명", "시험2", "시험2만점",
                "시험3명", "시험3", "시험3만점",
                "시험4명", "시험4", "시험4만점",
                "시험총점", "시험총만점", "숙제완성도",
                "선생님메모", "사이트링크",
            ],
        )
        reps = {item["key"]: item["value"] for item in replacements}
        self.assertEqual(reps["시험1명"], "단원평가")
        self.assertEqual(reps["시험1"], "92")
        self.assertEqual(reps["시험1만점"], "100")
        self.assertEqual(reps["숙제완성도"], "1/1 완료")
        self.assertNotIn("선생님메모1", reps)
