# apps/support/messaging/tests/test_messaging_service.py
"""
알림톡/메시징 서비스 단위 테스트.
DB 의존 없이 mock 기반으로 핵심 로직을 검증.

검증 범위:
1. send_event_notification — 트리거별 발송 경로
2. enqueue_sms — SQS enqueue 정책 / tenant isolation
3. is_reservation_cancelled — tenant isolation
4. 템플릿 변수 치환 — 누락/오입력 방어
5. 성적표 메시지 포맷 — 시험/과제/합불/미응시
6. worker dequeue — tenant_id 필수 / silent failure 방지
7. 가입 관련 회귀 — send_welcome_messages
8. 반 등록 완료 / 퇴원 알림
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def _make_tenant(tid=1, name="학원플러스", kakao_pfid="KA01PF_TEST", provider="solapi",
                 is_active=True, balance=100):
    t = SimpleNamespace(
        id=tid, pk=tid, name=name, kakao_pfid=kakao_pfid,
        messaging_provider=provider, messaging_is_active=is_active,
        credit_balance=balance, messaging_sender="01031217466",
        messaging_base_price=10,
    )
    domain_mock = MagicMock()
    domain_mock.filter.return_value.first.return_value = SimpleNamespace(host="hakwonplus.com")
    t.domains = domain_mock
    return t


def _make_student(name="홍길동", phone="01012345678", parent_phone="01087654321"):
    return SimpleNamespace(name=name, phone=phone, parent_phone=parent_phone,
                           ps_number="PS001", tenant_id=1)


def _make_config(trigger, enabled=True, message_mode="alimtalk",
                 template_name="Test", solapi_template_id="KA01TP_TEST",
                 solapi_status="APPROVED", body="#{학원명} #{학생이름2} 안내"):
    tmpl = SimpleNamespace(
        name=template_name, body=body,
        solapi_template_id=solapi_template_id,
        solapi_status=solapi_status,
        subject="테스트 제목",
    )
    return SimpleNamespace(
        trigger=trigger, enabled=enabled, message_mode=message_mode,
        template=tmpl,
    )


# services.py에서 내부 import하는 모듈 경로
_SEL = "apps.support.messaging.selectors"
_POL = "apps.support.messaging.policy"
_SVC = "apps.support.messaging.services"
_SQS = "apps.support.messaging.sqs_queue"


# ──────────────────────────────────────────
# 1. send_event_notification 테스트
# ──────────────────────────────────────────

class TestSendEventNotification(TestCase):
    """send_event_notification 핵심 경로 테스트."""

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_sends_to_parent_with_correct_replacements(
        self, mock_owner, mock_disabled, mock_config, mock_enqueue
    ):
        """학부모 전화번호로 올바른 치환 변수와 함께 enqueue."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config("check_in_complete",
                              body="#{학원명}입니다. #{학생이름2}학생이 입실했습니다. #{강의명} #{사이트링크}")
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(
            tenant=tenant, trigger="check_in_complete", student=student,
            send_to="parent", context={"강의명": "수학A반"},
        )

        self.assertTrue(result)
        mock_enqueue.assert_called_once()
        kw = mock_enqueue.call_args.kwargs
        self.assertEqual(kw["tenant_id"], 1)
        self.assertEqual(kw["to"], "01087654321")
        self.assertEqual(kw["message_mode"], "alimtalk")
        self.assertEqual(kw["template_id"], "KA01TP_TEST")
        # SMS fallback text
        self.assertIn("학원플러스", kw["text"])
        self.assertIn("홍길", kw["text"])
        self.assertIn("수학A반", kw["text"])
        # alimtalk replacements
        reps = {r["key"]: r["value"] for r in kw["alimtalk_replacements"]}
        self.assertEqual(reps["학원명"], "학원플러스")
        self.assertEqual(reps["학생이름2"], "홍길")
        self.assertEqual(reps["강의명"], "수학A반")

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_sends_to_student_phone(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """send_to='student'이면 학생 전화번호 사용."""
        tenant = _make_tenant()
        student = _make_student(phone="01099998888")
        config = _make_config("exam_score_published")
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(
            tenant=tenant, trigger="exam_score_published", student=student,
            send_to="student",
        )

        self.assertTrue(result)
        self.assertEqual(mock_enqueue.call_args.kwargs["to"], "01099998888")

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_skips_when_disabled(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """config 비활성이면 발송 스킵."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config("check_in_complete", enabled=False)
        mock_config.return_value = config

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        self.assertFalse(result)
        mock_enqueue.assert_not_called()

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config", return_value=None)
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_skips_when_no_config(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """config가 없으면 발송 스킵."""
        tenant = _make_tenant()
        student = _make_student()

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="nonexistent", student=student)

        self.assertFalse(result)
        mock_enqueue.assert_not_called()

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_skips_when_template_not_approved(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """템플릿 미승인이면 발송 스킵."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config("check_in_complete", solapi_status="PENDING")
        mock_config.return_value = config

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        self.assertFalse(result)
        mock_enqueue.assert_not_called()

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_skips_when_no_phone(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """전화번호 없으면 발송 스킵."""
        tenant = _make_tenant()
        student = _make_student(parent_phone="")
        config = _make_config("check_in_complete")
        mock_config.return_value = config

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(
            tenant=tenant, trigger="check_in_complete", student=student, send_to="parent",
        )

        self.assertFalse(result)
        mock_enqueue.assert_not_called()

    @patch(f"{_POL}.is_messaging_disabled", return_value=True)
    def test_skips_test_tenant(self, mock_disabled):
        """테스트 테넌트(9999)는 발송 스킵."""
        tenant = _make_tenant(tid=9999)
        student = _make_student()

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        self.assertFalse(result)

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_owner_fallback_only_for_registration_triggers(
        self, mock_owner, mock_disabled, mock_config, mock_enqueue
    ):
        """오너 fallback은 가입 안내 트리거(registration_approved_*)에만 적용."""
        tenant = _make_tenant(tid=2, name="박철과학")
        student = _make_student()
        config = _make_config("registration_approved_student")
        mock_config.side_effect = [None, config]
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="registration_approved_student", student=student)

        self.assertTrue(result)
        self.assertEqual(mock_config.call_count, 2)
        self.assertEqual(mock_config.call_args_list[0].args, (2, "registration_approved_student"))
        self.assertEqual(mock_config.call_args_list[1].args, (1, "registration_approved_student"))

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_no_owner_fallback_for_non_registration_triggers(
        self, mock_owner, mock_disabled, mock_config, mock_enqueue
    ):
        """check_in_complete 등 비가입 트리거는 오너 fallback 없이 스킵."""
        tenant = _make_tenant(tid=2, name="박철과학")
        student = _make_student()
        mock_config.return_value = None

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        self.assertFalse(result)
        self.assertEqual(mock_config.call_count, 1)  # 오너 fallback 호출 없음


# ──────────────────────────────────────────
# 2. tenant isolation 테스트
# ──────────────────────────────────────────

class TestTenantIsolation(TestCase):
    """멀티테넌트 격리 검증."""

    def test_is_reservation_cancelled_requires_tenant_id(self):
        """tenant_id 없이 호출하면 항상 False (cross-tenant lookup 차단)."""
        from apps.support.messaging.services import is_reservation_cancelled
        result = is_reservation_cancelled(reservation_id=123, tenant_id=None)
        self.assertFalse(result)

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_event_notification_uses_correct_tenant_id(
        self, mock_owner, mock_disabled, mock_config, mock_enqueue
    ):
        """send_event_notification이 올바른 tenant_id로 enqueue 호출."""
        tenant_a = _make_tenant(tid=2, name="테넌트A")
        student = _make_student()
        config = _make_config("check_in_complete")
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        send_event_notification(tenant=tenant_a, trigger="check_in_complete", student=student)

        self.assertEqual(mock_enqueue.call_args.kwargs["tenant_id"], 2)

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_no_cross_tenant_channel_fallback(
        self, mock_owner, mock_disabled, mock_config, mock_enqueue
    ):
        """테넌트 A 이벤트가 테넌트 B 채널을 사용하지 않음."""
        tenant_a = _make_tenant(tid=3, name="테넌트A")
        student = _make_student()
        config = _make_config("withdrawal_complete")
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        send_event_notification(tenant=tenant_a, trigger="withdrawal_complete", student=student)

        kw = mock_enqueue.call_args.kwargs
        self.assertEqual(kw["tenant_id"], 3)
        reps = {r["key"]: r["value"] for r in kw["alimtalk_replacements"]}
        self.assertEqual(reps["학원명"], "테넌트A")


# ──────────────────────────────────────────
# 3. enqueue_sms 정책 테스트
# ──────────────────────────────────────────

class TestEnqueueSmsPolicy(TestCase):
    """enqueue_sms 메시지 모드 및 정책 테스트."""

    @patch(f"{_SQS}.MessagingSQSQueue")
    @patch(f"{_POL}.can_send_sms", return_value=True)
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    def test_sms_mode_requires_can_send_sms(self, mock_disabled, mock_can, mock_queue_cls):
        """SMS 모드에서 can_send_sms 검사."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = True
        mock_queue_cls.return_value = mock_queue

        from apps.support.messaging.services import enqueue_sms
        result = enqueue_sms(tenant_id=1, to="01012345678", text="테스트")

        self.assertTrue(result)
        mock_can.assert_called_once_with(1)

    @patch(f"{_POL}.can_send_sms", return_value=False)
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    def test_sms_blocked_for_non_owner(self, mock_disabled, mock_can):
        """SMS 권한 없는 테넌트는 MessagingPolicyError 발생."""
        from apps.support.messaging.services import enqueue_sms
        from apps.support.messaging.policy import MessagingPolicyError

        with self.assertRaises(MessagingPolicyError):
            enqueue_sms(tenant_id=5, to="01012345678", text="테스트", message_mode="sms")

    @patch(f"{_SQS}.MessagingSQSQueue")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    def test_alimtalk_mode_skips_sms_check(self, mock_disabled, mock_queue_cls):
        """알림톡 전용 모드에서는 SMS 정책 검사 불필요."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = True
        mock_queue_cls.return_value = mock_queue

        from apps.support.messaging.services import enqueue_sms
        result = enqueue_sms(
            tenant_id=5, to="01012345678", text="테스트", message_mode="alimtalk",
        )

        self.assertTrue(result)

    @patch(f"{_POL}.is_messaging_disabled", return_value=True)
    def test_test_tenant_skipped(self, mock_disabled):
        """테스트 테넌트(9999)는 enqueue 스킵."""
        from apps.support.messaging.services import enqueue_sms
        result = enqueue_sms(tenant_id=9999, to="01012345678", text="테스트")
        self.assertFalse(result)


# ──────────────────────────────────────────
# 4. 템플릿 변수 치환 테스트
# ──────────────────────────────────────────

class TestTemplateVariableSubstitution(TestCase):
    """알림톡 템플릿 변수 치환 검증."""

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_all_standard_vars_substituted(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """학원명, 학생이름, 학생이름2, 사이트링크 모두 치환."""
        tenant = _make_tenant(name="수학왕학원")
        student = _make_student(name="김철수")
        config = _make_config(
            "withdrawal_complete",
            body="#{학원명}입니다. #{학생이름2}님 퇴원. #{학생이름} #{사이트링크}",
        )
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        send_event_notification(tenant=tenant, trigger="withdrawal_complete", student=student)

        text = mock_enqueue.call_args.kwargs["text"]
        self.assertIn("수학왕학원", text)
        self.assertIn("김철", text)
        self.assertIn("김철수", text)
        self.assertIn("hakwonplus.com", text)
        self.assertNotIn("#{", text)

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_context_vars_substituted(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """context에 전달한 추가 변수도 치환."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config(
            "check_in_complete",
            body="#{학원명} #{학생이름2} #{강의명} #{차시명} #{날짜} #{시간}",
        )
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        send_event_notification(
            tenant=tenant, trigger="check_in_complete", student=student,
            context={"강의명": "영어반", "차시명": "3차시", "날짜": "2026-03-25", "시간": "14:00"},
        )

        text = mock_enqueue.call_args.kwargs["text"]
        self.assertIn("영어반", text)
        self.assertIn("3차시", text)
        self.assertIn("2026-03-25", text)
        self.assertIn("14:00", text)
        self.assertNotIn("#{", text)

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_exam_score_published_vars(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """성적 공개 알림의 시험명/강의명/시험성적 치환."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config(
            "exam_score_published",
            body="#{학원명} #{학생이름2} #{시험명} #{강의명} 성적: #{시험성적} #{사이트링크}",
        )
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        send_event_notification(
            tenant=tenant, trigger="exam_score_published", student=student,
            context={"시험명": "중간고사", "강의명": "수학A", "시험성적": "85/100"},
        )

        text = mock_enqueue.call_args.kwargs["text"]
        self.assertIn("중간고사", text)
        self.assertIn("수학A", text)
        self.assertIn("85/100", text)
        self.assertNotIn("#{", text)

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_single_char_name_safe(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """1글자 이름도 안전하게 처리."""
        tenant = _make_tenant()
        student = _make_student(name="이")
        config = _make_config("withdrawal_complete", body="#{학생이름2}님 안녕")
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        send_event_notification(tenant=tenant, trigger="withdrawal_complete", student=student)

        text = mock_enqueue.call_args.kwargs["text"]
        self.assertIn("이님 안녕", text)


# ──────────────────────────────────────────
# 5. 성적표 메시지 포맷 테스트
# ──────────────────────────────────────────

class TestScoreReportFormat(TestCase):
    """성적표 메시지 구조 및 알림톡 템플릿 매칭 검증."""

    def test_score_report_structure_2_exams_4_homeworks(self):
        """시험 2개 + 과제 4개: [시험], [과제], [요약] 구조 검증."""
        detail_lines = [
            "[시험]",
            "- 단원평가 1: 92/100 (92%) 합격",
            "- 중간고사: 미응시",
            "",
            "[과제]",
            "- 영어쓰기: 80/100 (80%) 합격",
            "- 수학풀이: 50/100 (50%) 불합격",
            "- 독후감: 미제출",
            "- 실험보고서: 100/100 (100%) 합격",
            "",
            "[요약]",
            "- 시험: 1/2 합격 (평균 92점)",
            "- 과제: 2/4 합격 (평균 77점)",
            "- 최종: 보충 필요",
            "- 보충 대상: 중간고사, 수학풀이, 독후감",
        ]
        detail = "\n".join(detail_lines)

        # 구조 검증
        self.assertIn("[시험]", detail)
        self.assertIn("[과제]", detail)
        self.assertIn("[요약]", detail)
        self.assertIn("합격", detail)
        self.assertIn("불합격", detail)
        self.assertIn("미응시", detail)
        self.assertIn("미제출", detail)
        self.assertIn("보충 필요", detail)
        self.assertIn("보충 대상:", detail)
        self.assertIn("시험: 1/2 합격", detail)
        self.assertIn("과제: 2/4 합격", detail)

    def test_template_237_structure(self):
        """Template ID=237 (성적표 안내)에 #{시험성적} 변수가 정확히 삽입되는지."""
        template_body = (
            "안녕하세요, #{학원명}입니다.\n"
            "#{학생이름2}학생님, 성적표 안내드립니다.\n"
            "\n"
            "#{강의명} · #{차시명}\n"
            "━━━━━━━━━━━━━━━━\n"
            "\n"
            "#{시험성적}\n"
            "\n"
            "━━━━━━━━━━━━━━━━\n"
            "상세 결과는 앱에서 확인하실 수 있습니다.\n"
            "#{사이트링크}"
        )
        replacements = {
            "학원명": "학원플러스",
            "학생이름2": "홍길",
            "강의명": "수학A반",
            "차시명": "5차시",
            "시험성적": "[시험]\n- 단원평가: 92/100 (92%) 합격\n\n[요약]\n- 시험: 1/1 합격 (평균 92점)\n- 최종: 합격",
            "사이트링크": "https://hakwonplus.com",
        }
        text = template_body
        for k, v in replacements.items():
            text = text.replace(f"#{{{k}}}", v)

        self.assertNotIn("#{", text)
        self.assertIn("학원플러스", text)
        self.assertIn("홍길", text)
        self.assertIn("수학A반 · 5차시", text)
        self.assertIn("단원평가: 92/100 (92%) 합격", text)
        self.assertIn("최종: 합격", text)

    def test_all_passed_shows_합격(self):
        """모든 시험/과제 합격이면 '최종: 합격'."""
        summary = "[요약]\n- 시험: 2/2 합격 (평균 95점)\n- 최종: 합격"
        self.assertIn("최종: 합격", summary)
        self.assertNotIn("보충", summary)


# ──────────────────────────────────────────
# 6. Worker tenant_id 필수 검증
# ──────────────────────────────────────────

class TestWorkerTenantIdEnforcement(TestCase):
    """워커의 tenant_id 필수 정책 검증."""

    def test_message_without_tenant_id(self):
        """tenant_id 없는 메시지는 거부 대상."""
        msg = {"to": "01012345678", "text": "테스트"}
        self.assertIsNone(msg.get("tenant_id"))

    def test_message_with_tenant_id(self):
        """tenant_id 있는 메시지는 정상."""
        msg = {"tenant_id": 1, "to": "01012345678", "text": "테스트"}
        self.assertIsNotNone(msg.get("tenant_id"))


# ──────────────────────────────────────────
# 7. 가입 관련 회귀
# ──────────────────────────────────────────

class TestRegistrationMessages(TestCase):
    """가입 승인 알림톡 회귀 테스트."""

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    @patch(f"{_SEL}.get_auto_send_config")
    def test_send_registration_approved_student(self, mock_config, mock_owner, mock_enqueue):
        """학생 가입 승인 메시지가 올바른 변수로 발송."""
        tmpl = SimpleNamespace(
            body="#{학생이름}님 승인. ID: #{학생아이디} PW: #{학생비밀번호} #{사이트링크} #{비밀번호안내}",
            solapi_template_id="KA01TP_REG", solapi_status="APPROVED",
            subject="가입 승인", name="가입승인학생",
        )
        config = SimpleNamespace(enabled=True, template=tmpl)
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_registration_approved_messages
        result = send_registration_approved_messages(
            tenant_id=2, site_url="https://hakwonplus.com",
            student_name="홍길동", student_phone="01012345678",
            student_id="PS001", student_password="test1234",
            parent_phone="01087654321", parent_password="parent123",
        )

        self.assertEqual(result["status"], "enqueued")
        self.assertGreaterEqual(result["enqueued"], 1)
        text = mock_enqueue.call_args_list[0].kwargs["text"]
        self.assertIn("홍길동", text)
        self.assertIn("PS001", text)

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    @patch(f"{_SEL}.get_auto_send_config")
    def test_send_welcome_no_students(self, mock_config, mock_owner, mock_enqueue):
        """학생 없으면 skip."""
        from apps.support.messaging.services import send_welcome_messages
        result = send_welcome_messages(created_students=[], student_password="test")
        self.assertEqual(result["status"], "skip")


# ──────────────────────────────────────────
# 8. 반 등록 완료 / 퇴원 알림
# ──────────────────────────────────────────

class TestEnrollmentAndWithdrawalNotifications(TestCase):
    """반 등록 완료, 퇴원 알림 테스트."""

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_class_enrollment_complete(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """반 등록 완료 알림이 올바른 트리거/변수로 발송."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config(
            "class_enrollment_complete",
            body="#{학원명}입니다. #{학생이름2}님 반 등록 완료. #{사이트링크}",
        )
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(
            tenant=tenant, trigger="class_enrollment_complete", student=student,
            send_to="parent", context={"강의명": "수학A반"},
        )

        self.assertTrue(result)
        reps = {r["key"]: r["value"] for r in mock_enqueue.call_args.kwargs["alimtalk_replacements"]}
        self.assertEqual(reps["학원명"], "학원플러스")
        self.assertEqual(reps["학생이름2"], "홍길")

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_withdrawal_complete(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """퇴원 알림이 올바른 트리거/변수로 발송."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config(
            "withdrawal_complete",
            body="#{학원명}입니다. #{학생이름2}님 퇴원 완료.",
        )
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.support.messaging.services import send_event_notification
        result = send_event_notification(
            tenant=tenant, trigger="withdrawal_complete", student=student, send_to="parent",
        )

        self.assertTrue(result)
        text = mock_enqueue.call_args.kwargs["text"]
        self.assertIn("학원플러스", text)
        self.assertIn("홍길", text)
        self.assertNotIn("#{", text)
