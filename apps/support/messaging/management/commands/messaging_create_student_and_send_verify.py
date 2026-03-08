# apps/support/messaging/management/commands/messaging_create_student_and_send_verify.py
"""
1번 테넌트에 학부모 번호 01034137466인 학생을 생성하고, 해당 번호로 검증용 SMS 1건을 enqueue.
실제 발송은 워커가 SQS에서 꺼내 Solapi로 전송.

사용 (API 서버 또는 로컬):
  python manage.py messaging_create_student_and_send_verify
  python manage.py messaging_create_student_and_send_verify --tenant=1 --parent-phone=01034137466
"""
from django.core.management.base import BaseCommand

from apps.core.models import Tenant
from apps.domains.students.services.lecture_enroll import get_or_create_student_for_lecture_enroll
from apps.support.messaging.services import enqueue_sms
from apps.support.messaging.policy import MessagingPolicyError


class Command(BaseCommand):
    help = "Create a student (tenant 1, parent_phone 01034137466) and enqueue one verification SMS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=int,
            default=1,
            help="Tenant ID (default: 1)",
        )
        parser.add_argument(
            "--parent-phone",
            type=str,
            default="01034137466",
            help="Parent phone for student and recipient (default: 01034137466)",
        )
        parser.add_argument(
            "--name",
            type=str,
            default="메시지검증용",
            help="Student name (default: 메시지검증용)",
        )

    def handle(self, *args, **options):
        tenant_id = options["tenant"]
        parent_phone = (options["parent_phone"] or "").replace("-", "").strip()
        name = (options["name"] or "메시지검증용").strip()

        if not parent_phone or len(parent_phone) != 11 or not parent_phone.startswith("010"):
            self.stderr.write(
                self.style.ERROR("parent-phone must be 11 digits starting with 010.")
            )
            return

        tenant = Tenant.objects.filter(pk=tenant_id).first()
        if not tenant:
            self.stderr.write(self.style.ERROR(f"Tenant id={tenant_id} not found."))
            return

        sender = (tenant.messaging_sender or "").strip()
        if not sender:
            self.stderr.write(
                self.style.ERROR(
                    "Tenant has no messaging_sender. Set it in Message settings first."
                )
            )
            return

        item = {
            "name": name,
            "parent_phone": parent_phone,
            "phone": None,
        }
        password = "TempMsgVerify1!"
        student, created = get_or_create_student_for_lecture_enroll(
            tenant, item, password
        )
        if not student:
            self.stderr.write(
                self.style.ERROR("Failed to get or create student (e.g. duplicate phone).")
            )
            return

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created student id={student.id} name={student.name} parent_phone={parent_phone}"
                )
            )
        else:
            self.stdout.write(
                f"Using existing student id={student.id} name={student.name} parent_phone={parent_phone}"
            )

        text = "[학원플러스] 메시징 서비스 검증 발송입니다. 정상 동작을 확인했습니다."
        try:
            ok = enqueue_sms(
                tenant_id=tenant_id,
                to=parent_phone,
                text=text,
                sender=sender,
                message_mode="sms",
            )
        except MessagingPolicyError as e:
            self.stderr.write(self.style.ERROR(f"Policy error: {e}"))
            return

        if ok:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Enqueued 1 SMS to {parent_phone[:4]}****. Worker will send shortly."
                )
            )
        else:
            self.stderr.write(
                self.style.WARNING(
                    "enqueue_sms returned False (e.g. test tenant or whitelist skip)."
                )
            )
