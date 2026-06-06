# apps/support/messaging/management/commands/messaging_create_student_and_send_verify.py
"""
Legacy wrapper for the production common-owner Alimtalk verification command.

사용 (API 서버 또는 로컬):
  python manage.py messaging_create_student_and_send_verify
  python manage.py messaging_create_student_and_send_verify --tenant=3 --parent-phone=01031217466
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Legacy wrapper. Use messaging_verify_common_alimtalk for common-owner Alimtalk verification."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=int,
            default=3,
            help="Source tenant ID (default: 3)",
        )
        parser.add_argument(
            "--parent-phone",
            type=str,
            default="01031217466",
            help="Controlled verification recipient (default: 01031217466)",
        )
        parser.add_argument(
            "--name",
            type=str,
            default="",
            help="Display name for the verification message",
        )

    def handle(self, *args, **options):
        call_command(
            "messaging_verify_common_alimtalk",
            source_tenant_id=options["tenant"],
            phone=options["parent_phone"],
            name=options["name"],
            stdout=self.stdout,
            stderr=self.stderr,
        )
