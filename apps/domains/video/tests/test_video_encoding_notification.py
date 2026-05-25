from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from academy.adapters.db.django.repositories_video import _notify_video_encoding_complete
from apps.core.models import Tenant


class VideoEncodingNotificationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(name="Hakwon", code="hakwon")
        staff = SimpleNamespace(id=77, name="홍길동", phone="01012345678")
        lecture = SimpleNamespace(name="영어")
        session = SimpleNamespace(title="1차시", lecture=lecture)
        self.video = SimpleNamespace(
            id=123,
            title="문법 영상",
            uploaded_by=staff,
            session=session,
        )

    @patch("apps.domains.messaging.services.enqueue_sms")
    @patch("apps.domains.messaging.alimtalk_content_builders.get_solapi_template_id", return_value=None)
    @patch("apps.domains.messaging.selectors.get_auto_send_config", return_value=None)
    def test_video_encoding_complete_without_config_does_not_send_sms(
        self,
        mock_config,
        mock_unified_tid,
        mock_enqueue,
    ) -> None:
        _notify_video_encoding_complete(self.video, self.tenant.id)

        mock_enqueue.assert_not_called()

    @patch("apps.domains.messaging.services.enqueue_sms")
    @patch("apps.domains.messaging.alimtalk_content_builders.get_solapi_template_id", return_value=None)
    @patch("apps.domains.messaging.selectors.get_auto_send_config")
    def test_video_encoding_complete_without_approved_template_does_not_send_sms(
        self,
        mock_config,
        mock_unified_tid,
        mock_enqueue,
    ) -> None:
        mock_config.return_value = SimpleNamespace(
            enabled=True,
            template=SimpleNamespace(
                body="영상 인코딩이 완료되었습니다.",
                solapi_status="",
                solapi_template_id="",
            ),
            delay_mode="immediate",
            delay_value=None,
        )

        _notify_video_encoding_complete(self.video, self.tenant.id)

        mock_enqueue.assert_not_called()

    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    @patch("apps.domains.messaging.alimtalk_content_builders.get_solapi_template_id", return_value=None)
    @patch("apps.domains.messaging.selectors.get_auto_send_config")
    def test_video_encoding_complete_approved_template_uses_alimtalk_only(
        self,
        mock_config,
        mock_unified_tid,
        mock_enqueue,
    ) -> None:
        mock_config.return_value = SimpleNamespace(
            enabled=True,
            template=SimpleNamespace(
                body="#{선생님이름}님, #{영상명} 인코딩 완료",
                solapi_status="APPROVED",
                solapi_template_id="KA01VIDEO",
            ),
            delay_mode="immediate",
            delay_value=None,
        )

        _notify_video_encoding_complete(self.video, self.tenant.id)

        mock_enqueue.assert_called_once()
        kwargs = mock_enqueue.call_args.kwargs
        self.assertEqual(kwargs["message_mode"], "alimtalk")
        self.assertEqual(kwargs["template_id"], "KA01VIDEO")
        self.assertEqual(kwargs["event_type"], "video_encoding_complete")
        self.assertEqual(kwargs["source_domain"], "video")
        self.assertNotEqual(kwargs["message_mode"], "sms")
