"""Fixed, candidate-only account evidence. Credentials never leave this process."""
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.request

SCHEMA = "account-registration-development/v1"
SNAPSHOT_ACTION = "development.qa.account.snapshot"
MODES = {"verify", "snapshot", "compare"}
KINDS = {"fixed", "phone_last4", "random"}


class ProbeFailure(Exception):
    """Only fixed codes may cross the SSM boundary."""


def validate_arguments(student_id, mode, kind):
    if type(student_id) is not int or not 0 < student_id <= 9223372036854775807:
        raise ProbeFailure("target-invalid")
    if mode not in MODES or kind not in KINDS:
        raise ProbeFailure("arguments-invalid")


def scope_hash(tenant_code, tenant_id, release, digest):
    return hashlib.sha256(json.dumps(
        [tenant_code, tenant_id, release, digest], separators=(",", ":")
    ).encode()).hexdigest()


def fixture_prefix(tenant_code):
    return "qa-account-registration-" + hashlib.sha256(tenant_code.encode()).hexdigest()[:12] + "-"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeFailure("auth-redirect-refused")


def loopback_json(path, tenant_code, *, data=None, access=None):
    # There is deliberately no caller-supplied origin, path or HTTP method.
    if path not in {"/api/v1/token/", "/api/v1/core/me/", "/api/v1/student/me/"}:
        raise ProbeFailure("auth-path-invalid")
    headers = {"Content-Type": "application/json", "X-Tenant-Code": tenant_code}
    if access:
        headers["Authorization"] = "Bearer " + access
    request = urllib.request.Request(
        "http://127.0.0.1:8000" + path, headers=headers,
        data=json.dumps(data).encode() if data is not None else None,
        method="POST" if data is not None else "GET",
    )
    try:
        # Ignore proxy environment variables; refuse all redirect hops.
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(
            request, timeout=15
        ) as response:
            if response.status != 200:
                raise ProbeFailure("auth-status")
            body = response.read(131073)
            if len(body) > 131072:
                raise ProbeFailure("auth-response-size")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ProbeFailure("auth-response-shape")
            return result
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure("auth-request-failed") from None


def authenticate(user, role, student_id, tenant_code, kind, *, recover, check, display, http=loopback_json):
    raw = recover(user.account_notice_password_ciphertext, context=f"user:{user.tenant_id}:{user.pk}")
    if not raw:
        raise ProbeFailure("credential-unrecoverable")
    if not check(raw, user.password):  # No User.check_password setter, reset, or notice resolver.
        raise ProbeFailure("credential-hash-mismatch")
    if kind == "random" and not re.fullmatch(r"[0-9]{6}", raw):
        raise ProbeFailure("random-format-invalid")
    if kind == "phone_last4" and (not re.fullmatch(r"010[0-9]{8}", user.phone or "") or raw != user.phone[-4:]):
        raise ProbeFailure("own-phone-mismatch")
    token = http("/api/v1/token/", tenant_code,
                 data={"username": display(user), "password": raw, "tenant_code": tenant_code})
    access = token.get("access")
    if not isinstance(access, str) or not access or len(access) > 16384:
        raise ProbeFailure("auth-token-invalid")
    me = http("/api/v1/core/me/", tenant_code, access=access)
    if me.get("id") != user.pk or me.get("tenantRole") != role:
        raise ProbeFailure("auth-role-mismatch")
    projection = http("/api/v1/student/me/", tenant_code, access=access)
    if role == "student" and projection.get("id") != student_id:
        raise ProbeFailure("auth-student-mismatch")
    if role == "parent" and student_id not in [item.get("id") for item in (me.get("linkedStudents") or [])]:
        raise ProbeFailure("auth-parent-mismatch")
    return True


def canonical_state(objects):
    # Authentication may update last_login and produce disposable auth/audit rows.
    # Every other persisted field in the target business graph must stay identical.
    return [tuple((field.attname, str(getattr(obj, field.attname)))
                  for field in obj._meta.concrete_fields
                  if field.attname not in {"last_login", "updated_at"}) for obj in objects]


def runtime_scope(tenant):
    from django.conf import settings
    from apps.core.models import OpsAuditLog
    from apps.core.management.commands.setup_ymath_realuse_scenario import assert_isolated_runtime
    release, digest, capability = (os.environ.get(key, "") for key in ("QA_RELEASE", "QA_DIGEST", "QA_CAPABILITY"))
    if (not re.fullmatch(r"qa-ymath-realuse-fe-[0-9]+-[0-9]+-[a-f0-9]{12}", tenant.code)
            or not re.fullmatch(r"sha-[a-f0-9]{40}-run-[0-9]+-[0-9]+", release)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            or not re.fullmatch(r"[a-f0-9]{64}", capability)
            or os.environ.get("QA_TENANT") != tenant.code
            or os.environ.get("QA_TENANT_ID") != str(tenant.pk)
            or os.environ.get("DJANGO_SETTINGS_MODULE") != "apps.api.config.settings.development"
            or os.environ.get("ACADEMY_RUNTIME_ENV") != "development"
            or os.environ.get("ACADEMY_DEVELOPMENT_RELEASE_ID") != release
            or os.environ.get("QA_IMAGE") != "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api@" + digest
            or os.environ.get("SOLAPI_MOCK", "").lower() not in {"1", "true", "yes"}
            or settings.DATABASES["default"]["NAME"] != "academy_api_development"
            or settings.DATABASES["default"]["USER"] != "academy_api_development_app"):
        raise ProbeFailure("runtime-scope-invalid")
    assert_isolated_runtime()
    records = list(OpsAuditLog.objects.filter(action="development.qa.setup", target_tenant=tenant,
                                             result="success").values_list("payload", flat=True)[:2])
    expected = {"schema": "frontend-development-qa/v1", "tenant_code": tenant.code, "tenant_id": tenant.pk,
                "owner_sha256": hashlib.sha256(f"{tenant.code}:{tenant.pk}:{capability}".encode()).hexdigest()}
    if len(records) != 1 or records[0] != expected:
        raise ProbeFailure("owner-mismatch")
    return scope_hash(tenant.code, tenant.pk, release, digest)


def snapshot_seal(scope, parent_user):
    from django.conf import settings
    # Private keyed commitment only; neither password hash nor commitment is returned.
    return hmac.new(settings.SECRET_KEY.encode(),
                    f"{scope}:{parent_user.pk}:{parent_user.password}".encode(), hashlib.sha256).hexdigest()


def cleanup_snapshots(tenant):
    from apps.core.models import OpsAuditLog
    scope = runtime_scope(tenant)
    rows = OpsAuditLog.objects.filter(action=SNAPSHOT_ACTION, target_tenant=tenant)
    records = list(rows.values_list("payload", flat=True))
    if any(not isinstance(record, dict) or record.get("scope_sha256") != scope for record in records):
        raise ProbeFailure("snapshot-owner-mismatch")
    rows.delete()
    if rows.exists():
        raise ProbeFailure("snapshot-cleanup-failed")


def run_probe(tenant, student_id, mode, kind):
    validate_arguments(student_id, mode, kind)
    scope = runtime_scope(tenant)
    from django.contrib.auth.hashers import check_password
    from apps.core.models import OpsAuditLog, TenantMembership
    from apps.core.services.account_credentials import recover_password
    from apps.core.models.user import user_display_username
    from apps.domains.students.models import Student
    student = Student.objects.filter(pk=student_id, tenant=tenant, deleted_at__isnull=True,
                                     name__startswith=fixture_prefix(tenant.code)).select_related("user", "parent__user").first()
    if not student or not student.user_id or not student.parent_id or not student.parent.user_id:
        raise ProbeFailure("fixture-scope-invalid")
    parent = student.parent
    targets = [(student.user, "student"), (parent.user, "parent")]
    for user, role in targets:
        if user.tenant_id != tenant.pk or not TenantMembership.objects.filter(
            user=user, tenant=tenant, role=role, is_active=True
        ).exists():
            raise ProbeFailure("fixture-role-invalid")
    rows = OpsAuditLog.objects.filter(action=SNAPSHOT_ACTION, target_tenant=tenant, target_user=parent.user)
    preserved = None
    if mode == "snapshot":
        if rows.exists():
            raise ProbeFailure("snapshot-already-exists")
        OpsAuditLog.objects.create(action=SNAPSHOT_ACTION, target_tenant=tenant, target_user=parent.user,
                                  actor_username="account-registration-probe", result="success",
                                  payload={"scope_sha256": scope, "seal": snapshot_seal(scope, parent.user)})
    elif mode == "compare":
        records = list(rows.values_list("payload", flat=True)[:2])
        if (len(records) != 1 or set(records[0]) != {"scope_sha256", "seal"}
                or records[0]["scope_sha256"] != scope
                or not hmac.compare_digest(records[0]["seal"], snapshot_seal(scope, parent.user))):
            raise ProbeFailure("parent-hash-changed")
        preserved = True
    objects = [student, parent, student.user, parent.user]
    before = canonical_state(objects)
    for user, role in targets:
        authenticate(user, role, student.pk, tenant.code, kind if mode == "verify" else "fixed",
                     recover=recover_password, check=check_password, display=user_display_username)
    for obj in objects:
        obj.refresh_from_db()
    if canonical_state(objects) != before:
        raise ProbeFailure("business-state-changed")
    if mode == "compare":
        rows.delete()
    return {"schema": SCHEMA, "scope_sha256": scope, "mode": mode, "kind": kind,
            "credentials_valid": True, "token_me_valid": True, "role_count": 2,
            "random_format_valid": kind == "random" and mode == "verify",
            "parent_hash_preserved": preserved, "business_mutations": 0,
            "auth_observation_writes_allowed": True, "snapshot_count": rows.count()}
