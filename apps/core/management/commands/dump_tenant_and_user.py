# PATH: apps/core/management/commands/dump_tenant_and_user.py
"""
1번 테넌트 + 해당 테넌트 소속 admin97 유저(kjkszpj123) 정보를 확인·덤프.
AI 등 외부에 제공할 목적으로 복사하기 쉬운 텍스트로 출력.

사용:
  python manage.py dump_tenant_and_user
  python manage.py dump_tenant_and_user --tenant=1 --username=admin97 --password=kjkszpj123
"""
import json
from django.core.management.base import BaseCommand


def _safe_value(val):
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if hasattr(val, "pk"):
        return val.pk
    if isinstance(val, (str, int, float, bool)):
        return val
    return str(val)


def _serialize_model(inst, *, exclude=None):
    """모델 인스턴스를 공유 가능한 dict로 (password 등 제외)."""
    exclude = set(exclude or [])
    exclude.add("password")
    data = {}
    for f in inst._meta.get_fields():
        if f.name in exclude or (getattr(f, "remote_field", None) and f.concrete and f.name.endswith("_set")):
            continue
        if not f.concrete and f.name != "id":
            continue
        key = getattr(f, "attname", f.name)
        if not hasattr(inst, key):
            continue
        try:
            val = getattr(inst, key, None)
        except Exception:
            continue
        try:
            data[f.name] = _safe_value(val)
        except Exception:
            data[f.name] = "<non-serializable>"
    return data


class Command(BaseCommand):
    help = "1번 테넌트와 해당 테넌트 내 지정 유저(admin97 등) 정보를 덤프 (AI 제공용)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=int,
            default=1,
            help="테넌트 id (기본 1)",
        )
        parser.add_argument(
            "--username",
            default="admin97",
            help="유저명 (기본 admin97)",
        )
        parser.add_argument(
            "--password",
            default="kjkszpj123",
            help="비밀번호 확인용 (덤프에는 포함되지 않음)",
        )

    def handle(self, *args, **options):
        tenant_id = options["tenant"]
        username = options["username"]
        password = options["password"]

        out = {
            "tenant_id": tenant_id,
            "username": username,
            "password_check": None,
            "tenant": None,
            "tenant_domains": [],
            "program": None,
            "user": None,
            "membership": None,
            "errors": [],
        }

        from academy.adapters.db.django import repositories_core as core_repo

        # 1) Tenant
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            out["errors"].append(f"Tenant id={tenant_id} not found.")
            self._print(out)
            return
        out["tenant"] = _serialize_model(tenant)

        # 2) TenantDomain (해당 테넌트의 host 목록)
        out["tenant_domains"] = [
            {"id": d.id, "host": d.host, "is_primary": d.is_primary, "is_active": d.is_active}
            for d in core_repo.tenant_domain_filter_by_tenant(tenant)
        ]

        # 3) Program (tenant 1:1)
        program = core_repo.program_get_by_tenant(tenant)
        if program:
            out["program"] = _serialize_model(program)
        else:
            out["errors"].append("Program not found for this tenant.")

        # 4) User (username) + Membership (이 테넌트)
        user = core_repo.user_get_by_username(username)
        if not user:
            out["errors"].append(f"User username={username!r} not found.")
            self._print(out)
            return
        out["user"] = _serialize_model(user)

        # 비밀번호 검증만 수행 (해시는 출력하지 않음)
        if user.check_password(password):
            out["password_check"] = "OK"
        else:
            out["password_check"] = "MISMATCH"

        membership = core_repo.membership_get_full(tenant=tenant, user=user)
        if membership:
            out["membership"] = _serialize_model(membership)
        else:
            out["errors"].append(f"User {username} has no TenantMembership for tenant id={tenant_id}.")

        self._print(out)

    def _print(self, data):
        self.stdout.write("--- 아래 내용을 복사해 제공하면 됩니다 ---\n")
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        self.stdout.write("\n--- 끝 ---\n")
