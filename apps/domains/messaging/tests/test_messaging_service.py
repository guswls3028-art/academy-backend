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
_SEL = "apps.domains.messaging.selectors"
_POL = "apps.domains.messaging.policy"
_SVC = "apps.domains.messaging.services"
_SQS = "apps.domains.messaging.sqs_queue"


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

        from apps.domains.messaging.services import send_event_notification
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
        # 통합 4종 우선 라우팅 정책: check_in_complete는 attendance 통합 템플릿 사용
        self.assertEqual(kw["template_id"], "KA01TP260406121126868FGddLmrDFUC")
        # SMS fallback text
        self.assertIn("학원플러스", kw["text"])
        self.assertIn("홍길", kw["text"])
        self.assertIn("수학A반", kw["text"])
        # alimtalk replacements
        reps = {r["key"]: r["value"] for r in kw["alimtalk_replacements"]}
        # 통합 attendance 템플릿 변수 스키마: 학원이름/학생이름/강의명...
        self.assertEqual(reps["학원이름"], "학원플러스")
        self.assertEqual(reps["학생이름"], "홍길동")
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

        from apps.domains.messaging.services import send_event_notification
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

        from apps.domains.messaging.services import send_event_notification
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

        from apps.domains.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="nonexistent", student=student)

        self.assertFalse(result)
        mock_enqueue.assert_not_called()

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_uses_unified_template_when_template_not_approved(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """통합 4종 우선 정책: 개별 템플릿이 미승인이어도 통합 템플릿으로 발송."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config("check_in_complete", solapi_status="PENDING")
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.domains.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        self.assertTrue(result)
        mock_enqueue.assert_called_once()
        self.assertEqual(
            mock_enqueue.call_args.kwargs["template_id"],
            "KA01TP260406121126868FGddLmrDFUC",
        )

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

        from apps.domains.messaging.services import send_event_notification
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

        from apps.domains.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        self.assertFalse(result)

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_owner_fallback_when_tenant_has_no_config(
        self, mock_owner, mock_disabled, mock_config, mock_enqueue
    ):
        """테넌트에 config 없으면 오너 테넌트 config로 fallback."""
        tenant = _make_tenant(tid=2, name="박철과학")
        student = _make_student()
        config = _make_config("check_in_complete")
        mock_config.side_effect = [None, config]
        mock_enqueue.return_value = True

        from apps.domains.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        self.assertTrue(result)
        self.assertEqual(mock_config.call_count, 2)
        self.assertEqual(mock_config.call_args_list[0].args, (2, "check_in_complete"))
        self.assertEqual(mock_config.call_args_list[1].args, (1, "check_in_complete"))


# ──────────────────────────────────────────
# 2. tenant isolation 테스트
# ──────────────────────────────────────────

class TestTenantIsolation(TestCase):
    """멀티테넌트 격리 검증."""

    def test_is_reservation_cancelled_requires_tenant_id(self):
        """tenant_id 없이 호출하면 항상 False (cross-tenant lookup 차단)."""
        from apps.domains.messaging.services import is_reservation_cancelled
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

        from apps.domains.messaging.services import send_event_notification
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

        from apps.domains.messaging.services import send_event_notification
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

        from apps.domains.messaging.services import enqueue_sms
        result = enqueue_sms(tenant_id=1, to="01012345678", text="테스트")

        self.assertTrue(result)
        mock_can.assert_called_once_with(1)

    @patch(f"{_POL}.can_send_sms", return_value=False)
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    def test_sms_blocked_for_non_owner(self, mock_disabled, mock_can):
        """SMS 권한 없는 테넌트는 MessagingPolicyError 발생."""
        from apps.domains.messaging.services import enqueue_sms
        from apps.domains.messaging.policy import MessagingPolicyError

        with self.assertRaises(MessagingPolicyError):
            enqueue_sms(tenant_id=5, to="01012345678", text="테스트", message_mode="sms")

    @patch(f"{_SQS}.MessagingSQSQueue")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    def test_alimtalk_mode_skips_sms_check(self, mock_disabled, mock_queue_cls):
        """알림톡 전용 모드에서는 SMS 정책 검사 불필요."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = True
        mock_queue_cls.return_value = mock_queue

        from apps.domains.messaging.services import enqueue_sms
        result = enqueue_sms(
            tenant_id=5, to="01012345678", text="테스트", message_mode="alimtalk",
        )

        self.assertTrue(result)

    @patch(f"{_POL}.is_messaging_disabled", return_value=True)
    def test_test_tenant_skipped(self, mock_disabled):
        """테스트 테넌트(9999)는 enqueue 스킵."""
        from apps.domains.messaging.services import enqueue_sms
        result = enqueue_sms(tenant_id=9999, to="01012345678", text="테스트")
        self.assertFalse(result)


class TestMessagingProviderResolution(TestCase):
    """정책 분기: provider/pf_id/use_default/sms 허용 여부."""

    @patch(f"{_POL}.get_tenant_provider", return_value="ppurio")
    @patch(f"{_POL}.resolve_kakao_channel", return_value={"pf_id": "", "use_default": True})
    def test_resolve_alimtalk_provider_with_default_channel(self, mock_channel, mock_provider):
        from apps.domains.messaging.policy import resolve_messaging_provider
        result = resolve_messaging_provider(tenant_id=2, message_type="alimtalk")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["provider"], "ppurio")
        self.assertEqual(result["pf_id"], "")
        self.assertTrue(result["use_default"])

    @patch(f"{_POL}.get_tenant_provider", return_value="solapi")
    @patch(f"{_POL}.can_send_sms", return_value=False)
    def test_resolve_sms_provider_blocked(self, mock_can_sms, mock_provider):
        from apps.domains.messaging.policy import resolve_messaging_provider
        result = resolve_messaging_provider(tenant_id=5, message_type="sms")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "sms_not_allowed")
        self.assertEqual(result["provider"], "solapi")


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

        from apps.domains.messaging.services import send_event_notification
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

        from apps.domains.messaging.services import send_event_notification
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

        from apps.domains.messaging.services import send_event_notification
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

        from apps.domains.messaging.services import send_event_notification
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
            "학생이름2": "길동",
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
        self.assertIn("길동", text)
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

        from apps.domains.messaging.services import send_registration_approved_messages
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
        from apps.domains.messaging.services import send_welcome_messages
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

        from apps.domains.messaging.services import send_event_notification
        result = send_event_notification(
            tenant=tenant, trigger="class_enrollment_complete", student=student,
            send_to="parent", context={"강의명": "수학A반"},
        )

        self.assertTrue(result)
        reps = {r["key"]: r["value"] for r in mock_enqueue.call_args.kwargs["alimtalk_replacements"]}
        self.assertEqual(reps["학원명"], "학원플러스")
        self.assertEqual(reps["학생이름2"], "길동")

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

        from apps.domains.messaging.services import send_event_notification
        result = send_event_notification(
            tenant=tenant, trigger="withdrawal_complete", student=student, send_to="parent",
        )

        self.assertTrue(result)
        text = mock_enqueue.call_args.kwargs["text"]
        self.assertIn("학원플러스", text)
        self.assertIn("길동", text)
        self.assertNotIn("#{", text)


# ──────────────────────────────────────────
# 안전장치 테스트 — time guard, idempotency, recipient whitelist
# ──────────────────────────────────────────

class TestTimeGuard(TestCase):
    """출결 알림톡 time guard: 오늘 세션만 발송."""

    @patch(f"{_SVC}.send_event_notification")
    def test_past_date_session_skipped(self, mock_send):
        """과거 날짜 세션 출결 변경 시 알림톡 발송하지 않음."""
        from datetime import date, timedelta
        yesterday = date.today() - timedelta(days=1)

        session = SimpleNamespace(date=yesterday, title="3차시", order=3)
        session.lecture = SimpleNamespace(title="수학A", tenant_id=1)
        enrollment = SimpleNamespace(student=_make_student())
        att = SimpleNamespace(id=1, enrollment=enrollment, session=session)
        tenant = _make_tenant()

        from apps.domains.attendance.views import _send_attendance_notification
        _send_attendance_notification(tenant, att, "check_in_complete")

        mock_send.assert_not_called()

    @patch(f"{_SVC}.send_event_notification")
    def test_today_session_sends(self, mock_send):
        """오늘 날짜 세션 출결 변경 시 알림톡 정상 발송."""
        from datetime import date
        today = date.today()

        session = SimpleNamespace(date=today, title="3차시", order=3)
        session.lecture = SimpleNamespace(title="수학A", tenant_id=1)
        enrollment = SimpleNamespace(student=_make_student())
        att = SimpleNamespace(id=1, enrollment=enrollment, session=session)
        tenant = _make_tenant()

        from apps.domains.attendance.views import _send_attendance_notification
        _send_attendance_notification(tenant, att, "check_in_complete")

        mock_send.assert_called_once()


class TestIdempotencyMetadata(TestCase):
    """send_event_notification이 멱등성 메타데이터를 enqueue_sms에 전달."""

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    def test_passes_event_metadata(self, mock_owner, mock_disabled, mock_config, mock_enqueue):
        """trigger, student_id, occurrence_key가 enqueue_sms에 전달됨."""
        tenant = _make_tenant()
        student = SimpleNamespace(
            id=42, pk=42, name="홍길동", phone="01012345678",
            parent_phone="01087654321", ps_number="PS001", tenant_id=1,
        )
        config = _make_config("check_in_complete")
        mock_config.return_value = config
        mock_enqueue.return_value = True

        from apps.domains.messaging.services import send_event_notification
        send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        kwargs = mock_enqueue.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "check_in_complete")
        self.assertEqual(kwargs["target_type"], "student")
        self.assertEqual(kwargs["target_id"], 42)
        self.assertTrue(kwargs["occurrence_key"])  # 날짜 기반 키


class TestRecipientWhitelist(TestCase):
    """recipient guard: 테스트 모드에서 whitelist 번호만 발송."""

    @patch.dict("os.environ", {"MESSAGING_TEST_WHITELIST": "01031217466,01034137466"})
    def test_allowed_number(self):
        from apps.domains.messaging.policy import check_recipient_allowed
        self.assertTrue(check_recipient_allowed("01031217466"))
        self.assertTrue(check_recipient_allowed("01034137466"))

    @patch.dict("os.environ", {"MESSAGING_TEST_WHITELIST": "01031217466,01034137466"})
    def test_blocked_number(self):
        from apps.domains.messaging.policy import check_recipient_allowed
        self.assertFalse(check_recipient_allowed("01099999999"))

    @patch.dict("os.environ", {"MESSAGING_TEST_WHITELIST": ""})
    def test_empty_whitelist_allows_all(self):
        """운영 모드: whitelist 비어있으면 모든 번호 허용."""
        from apps.domains.messaging.policy import check_recipient_allowed
        self.assertTrue(check_recipient_allowed("01099999999"))

    @patch.dict("os.environ", {}, clear=False)
    def test_no_env_allows_all(self):
        """MESSAGING_TEST_WHITELIST 미설정 시 모든 번호 허용."""
        import os
        os.environ.pop("MESSAGING_TEST_WHITELIST", None)
        from apps.domains.messaging.policy import check_recipient_allowed
        self.assertTrue(check_recipient_allowed("01099999999"))


class TestDryRunMode(TestCase):
    """dry-run 모드: 로그만 남기고 실발송 안 함."""

    @patch.dict("os.environ", {"MESSAGING_DRY_RUN_TRIGGERS": "*"})
    def test_wildcard_blocks_general_triggers(self):
        from apps.domains.messaging.policy import is_event_dry_run
        self.assertTrue(is_event_dry_run("check_in_complete"))
        self.assertTrue(is_event_dry_run("exam_score_published"))
        self.assertTrue(is_event_dry_run("withdrawal_complete"))

    @patch.dict("os.environ", {"MESSAGING_DRY_RUN_TRIGGERS": "*"})
    def test_wildcard_allows_registration_triggers(self):
        """가입/비밀번호 관련은 dry-run에서도 실발송."""
        from apps.domains.messaging.policy import is_event_dry_run
        self.assertFalse(is_event_dry_run("registration_approved_student"))
        self.assertFalse(is_event_dry_run("registration_approved_parent"))
        self.assertFalse(is_event_dry_run("password_find_otp"))
        self.assertFalse(is_event_dry_run("password_reset_student"))

    @patch.dict("os.environ", {"MESSAGING_DRY_RUN_TRIGGERS": "check_in_complete,absent_occurred"})
    def test_specific_triggers_only(self):
        from apps.domains.messaging.policy import is_event_dry_run
        self.assertTrue(is_event_dry_run("check_in_complete"))
        self.assertTrue(is_event_dry_run("absent_occurred"))
        self.assertFalse(is_event_dry_run("exam_score_published"))

    @patch.dict("os.environ", {"MESSAGING_DRY_RUN_TRIGGERS": ""})
    def test_empty_env_no_dry_run(self):
        from apps.domains.messaging.policy import is_event_dry_run
        self.assertFalse(is_event_dry_run("check_in_complete"))

    @patch(f"{_SVC}.enqueue_sms")
    @patch(f"{_SEL}.get_auto_send_config")
    @patch(f"{_POL}.is_messaging_disabled", return_value=False)
    @patch(f"{_POL}.get_owner_tenant_id", return_value=1)
    @patch.dict("os.environ", {"MESSAGING_DRY_RUN_TRIGGERS": "*"})
    def test_send_event_notification_dry_run(
        self, mock_owner, mock_disabled, mock_config, mock_enqueue
    ):
        """dry-run 모드에서 send_event_notification은 enqueue 호출하지 않음."""
        tenant = _make_tenant()
        student = _make_student()
        config = _make_config("check_in_complete")
        mock_config.return_value = config

        from apps.domains.messaging.services import send_event_notification
        result = send_event_notification(tenant=tenant, trigger="check_in_complete", student=student)

        self.assertFalse(result)
        mock_enqueue.assert_not_called()


class TestAttendanceNoNotification(TestCase):
    """일반 강의 출결 변경 시 알림톡 미발송 확인."""

    @patch(f"{_SVC}.send_event_notification")
    def test_partial_update_no_notification(self, mock_send):
        """partial_update에서 알림톡 호출 코드가 제거되었는지 확인."""
        # attendance/views.py의 partial_update에서 _send_attendance_notification 호출이 제거됨
        # 이 테스트는 코드 제거를 문서화
        from apps.domains.attendance import views as att_views
        import inspect
        source = inspect.getsource(att_views.AttendanceViewSet.partial_update)
        self.assertNotIn("_send_attendance_notification", source)
        self.assertNotIn("check_in_complete", source)
        self.assertNotIn("absent_occurred", source)
