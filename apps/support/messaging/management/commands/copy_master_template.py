# apps/support/messaging/management/commands/copy_master_template.py
"""
4단계: 마스터 템플릿을 학원 PFID로 복사하여 검수 신청

선생님이 PFID 연동 후, 현진님 계정의 마스터 템플릿을 해당 학원 PFID로 복사하고
카카오 검수 신청을 넣는 자동화.

사용:
  python manage.py copy_master_template --tenant=academy-code
  python manage.py copy_master_template --tenant=1  # tenant id

환경변수:
  SOLAPI_MASTER_TEMPLATE_ID: 마스터 템플릿 ID (현진님 승인 템플릿)
  SOLAPI_API_KEY, SOLAPI_API_SECRET: Solapi 인증

실제 연동 시 Solapi/Kakao 비즈니스 API에서
- 채널 공유(파트너) 여부 조회
- 템플릿 복사 및 검수 신청
호출을 추가하면 됨. 현재는 스켈레톤 + 로그.
"""
from django.core.management.base import BaseCommand
from django.conf import settings

from apps.core.models import Tenant


class Command(BaseCommand):
    help = "Copy master alimtalk template to tenant PFID and submit for review (skeleton)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            required=True,
            help="Tenant code or id",
        )
        parser.add_argument(
            "--master-template",
            type=str,
            default=None,
            help="Master template ID (default: SOLAPI_MASTER_TEMPLATE_ID env)",
        )

    def handle(self, *args, **options):
        tenant_arg = options["tenant"].strip()
        master_id = options["master_template"] or getattr(
            settings, "SOLAPI_MASTER_TEMPLATE_ID", None
        ) or __import__("os").environ.get("SOLAPI_MASTER_TEMPLATE_ID", "")

        if not master_id:
            self.stdout.write(
                self.style.WARNING("SOLAPI_MASTER_TEMPLATE_ID not set, using placeholder")
            )
            master_id = "MASTER_TEMPLATE_ID"

        # Resolve tenant
        tenant = None
        if tenant_arg.isdigit():
            tenant = Tenant.objects.filter(pk=int(tenant_arg)).first()
        if not tenant:
            tenant = Tenant.objects.filter(code=tenant_arg).first()
        if not tenant:
            self.stderr.write(self.style.ERROR(f"Tenant not found: {tenant_arg}"))
            return 1

        pfid = (tenant.kakao_pfid or "").strip()
        if not pfid:
            self.stderr.write(
                self.style.ERROR(f"Tenant {tenant.code} has no kakao_pfid. Link first.")
            )
            return 1

        self.stdout.write(
            f"Would copy master_template_id={master_id} to tenant={tenant.code} pfid={pfid}"
        )
        self.stdout.write(
            "Implement: 1) Check channel shared (partner). 2) Copy template to PFID. 3) Submit review."
        )
        # TODO: Solapi/Kakao API 호출
        # - 채널 공유 확인
        # - 템플릿 복사 (마스터 → 학원 PFID)
        # - 검수 신청
        self.stdout.write(self.style.SUCCESS("Done (no-op until API wired)"))
        return 0
