# PATH: apps/core/management/commands/ensure_tenant_owner.py
"""
테넌트에 Owner(원장) 멤버십 확보. 직원관리 목록 상단 "대표" 행 표시용.

직원관리 "대표" 행은 Staff 테이블이 아니라 TenantMembership(role=owner) 또는
tenant.owner_name / 현재 사용자 폴백으로 표시됨. 1번 테넌트에서 오너가 안 뜨면
이 테넌트에 Owner 멤버십이 없는 것이므로, 이 명령으로 등록.

사용 (기존 유저를 1번 테넌트 오너로):
  python manage.py ensure_tenant_owner hakwonplus --username=로그인아이디

유저 없으면 생성 (비밀번호 필수):
  python manage.py ensure_tenant_owner hakwonplus --username=원장아이디 --password=비밀번호 --name=원장이름
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model

from academy.adapters.db.django import repositories_core as core_repo
from apps.core.models.user import user_internal_username, user_display_username

User = get_user_model()


class Command(BaseCommand):
    help = "Ensure a tenant has an owner (TenantMembership role=owner). Staff list '대표' row uses this."

    def add_arguments(self, parser):
        parser.add_argument(
            "code",
            type=str,
            help="Tenant code (e.g. hakwonplus for 1번, 9999 or admin97 for 로컬)",
        )
        parser.add_argument(
            "--username",
            type=str,
            required=True,
            help="Login username (로그인 아이디). 해당 테넌트 소속 유저로 조회/생성.",
        )
        parser.add_argument(
            "--password",
            type=str,
            default=None,
            help="Password (유저 없을 때 생성 시 필수)",
        )
        parser.add_argument(
            "--name",
            type=str,
            default="",
            help="Display name (유저 생성 시 사용)",
        )

    def handle(self, *args, **options):
        code = (options["code"] or "").strip()
        username = (options["username"] or "").strip()
        password = options.get("password")
        name = (options.get("name") or "").strip()

        if not code:
            self.stderr.write(self.style.ERROR("Tenant code is required."))
            return
        if not username:
            self.stderr.write(self.style.ERROR("--username is required."))
            return

        tenant = core_repo.tenant_get_by_code(code)
        if not tenant:
            self.stderr.write(self.style.ERROR(f"Tenant code '{code}' not found."))
            return

        with transaction.atomic():
            user = core_repo.user_get_by_tenant_username(tenant, username)
            if user:
                self.stdout.write(f"User found: id={user.id} username={user_display_username(user)}")
            else:
                if not password:
                    self.stderr.write(
                        self.style.ERROR("User not found. --password is required to create a new user.")
                    )
                    return
                internal = user_internal_username(tenant, username)
                user = User.objects.create_user(
                    username=internal,
                    password=password,
                    tenant=tenant,
                    email="",
                    name=name or username,
                    phone="",
                )
                self.stdout.write(self.style.SUCCESS(f"Created User: {user_display_username(user)}"))

            membership = core_repo.membership_ensure_active(tenant=tenant, user=user, role="owner")
            if membership.role != "owner":
                membership.role = "owner"
                membership.save(update_fields=["role"])
                self.stdout.write(self.style.SUCCESS(f"Updated TenantMembership to role=owner"))
            else:
                self.stdout.write(f"TenantMembership already owner: {tenant.code} / {user_display_username(user)}")

            if not (getattr(tenant, "owner_name", None) or "").strip():
                display = (getattr(user, "name", None) or "").strip() or user_display_username(user) or "원장"
                tenant.owner_name = display[:100]
                tenant.save(update_fields=["owner_name"])
                self.stdout.write(self.style.SUCCESS(f"Set tenant.owner_name = {tenant.owner_name!r}"))

        self.stdout.write(
            self.style.SUCCESS(f"Done. Tenant {tenant.code} (id={tenant.id}) now has owner: {user_display_username(user)}")
        )
