from unittest.mock import patch

from django.test import TestCase

from academy.adapters.db.django.repositories_video import _notify_video_encoding_complete, job_complete
from apps.core.models import Tenant, TenantDomain
from apps.core.r2_paths import video_hls_master_path
from apps.domains.messaging.default_templates import get_default_templates
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, ScheduledNotification
from apps.domains.staffs.models import Staff
from apps.domains.video.models import Video, VideoTranscodeJob
from apps.worker.messaging_worker.sqs_main import _allowed_common_template_ids, _video_encoding_block_reason


class VideoEncodingNotificationTests(TestCase):
    trigger = "video_encoding_complete"

    def setUp(self):
        self.owner = Tenant.objects.create(name="공용", code="video-owner")
        self.tenant = Tenant.objects.create(name="예시학원", code="video-academy", messaging_is_active=True)
        self.enterContext(self.settings(OWNER_TENANT_ID=self.owner.id))
        TenantDomain.objects.update_or_create(tenant=self.tenant, is_primary=True, defaults={"host": "video-academy.example.test"})
        staff = Staff.objects.create(tenant=self.tenant, name="담당자", phone="01012345678")
        self.video = Video.objects.create(tenant=self.tenant, uploaded_by=staff, title="합성 영상")
        default = get_default_templates(self.tenant.name)[self.trigger]
        self.local = MessageTemplate.objects.create(tenant=self.tenant, name=default["name"], body=default["body"])
        self.config = AutoSendConfig.objects.create(
            tenant=self.tenant, trigger=self.trigger, template=self.local, enabled=True, message_mode="alimtalk",
        )
        self.approved = MessageTemplate.objects.create(
            tenant=self.owner, name="공용 영상 완료", body="#{선생님이름}님, #{영상명} 완료. #{사이트링크}",
            solapi_status="APPROVED", solapi_template_id="KA01-VIDEO",
        )
        self.owner_config = AutoSendConfig.objects.create(
            tenant=self.owner, trigger=self.trigger, template=self.approved, enabled=False,
        )

    def notify(self):
        _notify_video_encoding_complete(self.video, self.tenant.id)

    @patch("apps.domains.messaging.sqs_queue.MessagingSQSQueue")
    def test_default_content_dispatches_exact_owner_envelope_to_uploader_once(self, queue):
        queue.return_value.enqueue.return_value = True
        job = VideoTranscodeJob.objects.create(video=self.video, tenant=self.tenant)
        self.video.current_job = job
        self.video.save(update_fields=["current_job"])
        path = video_hls_master_path(tenant_id=self.tenant.id, video_id=self.video.id)
        self.enterContext(patch("apps.domains.video.services.video_job_lock.release"))
        self.enterContext(patch("academy.adapters.db.django.repositories_video._cache_video_status_safe"))
        self.enterContext(patch("apps.domains.video.redis_status_cache.delete_video_progress_key"))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(job_complete(str(job.id), path), (True, "ok"))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(job_complete(str(job.id), path), (True, "idempotent"))
        queue.return_value.enqueue.assert_called_once()
        self.assertEqual(ScheduledNotification.objects.filter(trigger=self.trigger).count(), 1)
        payload = queue.return_value.enqueue.call_args.kwargs
        self.assertEqual(payload["tenant_id"], self.owner.id)
        self.assertEqual(payload["source_tenant_id"], self.tenant.id)
        self.assertEqual((payload["target_type"], payload["target_id"]), ("staff", self.video.uploaded_by_id))
        self.assertEqual(payload["message_mode"], "alimtalk")
        self.assertEqual(payload["event_type"], self.trigger)
        self.assertEqual(payload["template_id"], self.approved.solapi_template_id)
        self.assertEqual(payload["text"], "담당자님, 합성 영상 완료. https://video-academy.example.test")
        self.assertEqual({r["key"] for r in payload["alimtalk_replacements"]}, {"선생님이름", "영상명", "사이트링크"})
        self.assertEqual(_allowed_common_template_ids(self.trigger), {payload["template_id"]})
        self.assertEqual(_video_encoding_block_reason(self.trigger, "alimtalk", payload["template_id"]), "")
        self.assertTrue(_video_encoding_block_reason(self.trigger, "sms", payload["template_id"]))

    def test_customer_edits_do_not_change_approved_scheduled_content(self):
        self.local.body = "고객이 작성한 다른 문구"
        self.local.save(update_fields=["body"])
        self.config.delay_mode, self.config.delay_value = "delay_minutes", 10
        self.config.save(update_fields=["delay_mode", "delay_value"])
        self.notify()
        payload = ScheduledNotification.objects.get(trigger=self.trigger).payload
        self.assertNotIn(self.local.body, payload["text"])
        self.assertEqual(payload["template_id"], self.approved.solapi_template_id)

    def test_local_provider_id_cannot_replace_unapproved_or_missing_owner_envelope(self):
        self.local.solapi_status, self.local.solapi_template_id = "APPROVED", "KA-OTHER"
        self.local.save(update_fields=["solapi_status", "solapi_template_id"])
        self.approved.solapi_status = "PENDING"
        self.approved.save(update_fields=["solapi_status"])
        self.notify()
        self.owner_config.delete()
        self.notify()
        self.assertFalse(ScheduledNotification.objects.exists())

    def test_delivery_configuration_and_tenant_ownership_fail_closed(self):
        for field, value in (("enabled", False), ("message_mode", "sms"), ("template", self.approved)):
            with self.subTest(field=field):
                original = getattr(self.config, field)
                setattr(self.config, field, value)
                self.config.save(update_fields=[field])
                self.notify()
                self.assertFalse(ScheduledNotification.objects.exists())
                setattr(self.config, field, original)
                self.config.save(update_fields=[field])
        self.video.uploaded_by.tenant_id = self.owner.id
        self.notify()
        self.assertFalse(ScheduledNotification.objects.exists())

    def test_no_school_opt_in_does_not_enqueue(self):
        self.config.delete()
        self.notify()
        self.assertFalse(ScheduledNotification.objects.exists())

    def test_foreign_owner_template_and_inactive_business_messaging_do_not_enqueue(self):
        self.owner_config.template = self.local
        self.owner_config.save(update_fields=["template"])
        self.notify()
        self.assertFalse(ScheduledNotification.objects.exists())
        self.owner_config.template = self.approved
        self.owner_config.save(update_fields=["template"])
        self.tenant.messaging_is_active = False
        self.tenant.save(update_fields=["messaging_is_active"])
        self.notify()
        self.assertFalse(ScheduledNotification.objects.exists())
