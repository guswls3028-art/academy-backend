"""Candidate QA JWTs are tied to the admitted tenant and lease attempt."""

from contextlib import contextmanager
import time
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from rest_framework.authentication import SessionAuthentication
from rest_framework.test import APIClient
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.api.common.auth_jwt import (
    TenantAwareTokenObtainPairSerializer,
    TenantAwareTokenRefreshSerializer,
)
from apps.api.middleware import candidate_qa_admission as admission
from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.authentication import TenantAwareSessionAuthentication, TokenVersionJWTAuthentication
from apps.core.tenant.context import clear_current_tenant


@pytest.fixture
def qa_env(monkeypatch):
    monkeypatch.setenv("CANDIDATE_LEASE_REQUIRED", "true")
    monkeypatch.setenv("ACADEMY_RUNTIME_ENV", "development")
    monkeypatch.setenv("ACADEMY_QA_MODE", "isolated-qa")
    monkeypatch.setenv("ACADEMY_QA_LEASE_ID", "a" * 32)
    monkeypatch.setenv("ACADEMY_QA_BINDING_SHA256", "b" * 64)


@pytest.fixture
def account(db, qa_env, monkeypatch):
    tenant = Tenant.objects.create(name="Candidate QA", code="candidate-qa-auth", is_active=True)
    user = get_user_model().objects.create_user(
        username=user_internal_username(tenant, "qa-teacher"),
        password="qa-password-123",
        tenant=tenant,
        token_version=0,
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="teacher")
    lease = {
        "lease_id": "a" * 32,
        "revision": 3,
        "lock_owner": "candidate:9:1",
        "binding_sha256": "b" * 64,
        "source_sha": "c" * 40,
        "images": {"api": "sha256:" + "d" * 64},
        "tenant_ids": [tenant.id],
        "expires_at": int(time.time()) + 900,
    }
    lease["state"] = "active"
    monkeypatch.setattr(
        "apps.infrastructure.qa_lease.get_admission_gate",
        lambda kind: SimpleNamespace(inspect_lease=lambda: lease, admit=lambda: lease),
    )
    return tenant, user, lease


def _request(lease):
    return SimpleNamespace(
        META={"HTTP_X_TENANT_CODE": "candidate-qa-auth"},
        data={},
        candidate_qa_lease=lease,
        get_host=lambda: "api.hakwonplus.com",
    )


def _login(lease):
    serializer = TenantAwareTokenObtainPairSerializer(
        data={"username": "qa-teacher", "password": "qa-password-123"},
        context={"request": _request(lease)},
    )
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@pytest.mark.django_db
def test_qa_login_binds_both_tokens_and_caps_ttl(account):
    tenant, _, lease = account
    tokens = _login(lease)
    refresh = RefreshToken(tokens["refresh"])
    access = AccessToken(tokens["access"])
    for token in (refresh, access):
        assert token["tenant_id"] == tenant.id
        assert token["qa_lease_id"] == lease["lease_id"]
        assert token["qa_revision"] == lease["revision"]
        assert token["qa_lock_owner"] == lease["lock_owner"]
        assert token["qa_binding_sha256"] == lease["binding_sha256"]
        assert token["qa_source_sha"] == lease["source_sha"]
        assert token["qa_api_digest"] == lease["images"]["api"]
        assert token["exp"] <= lease["expires_at"]


@pytest.mark.django_db
def test_qa_login_rejects_out_of_scope_and_multiple_memberships(account):
    tenant, user, lease = account
    lease["tenant_ids"] = [tenant.id + 1]
    with pytest.raises(AuthenticationFailed):
        _login(lease)
    lease["tenant_ids"] = [tenant.id]
    other = Tenant.objects.create(name="Second QA", code="candidate-qa-second", is_active=True)
    TenantMembership.ensure_active(tenant=other, user=user, role="teacher")
    with pytest.raises(AuthenticationFailed):
        _login(lease)


@pytest.mark.django_db
def test_qa_login_rejects_drain_or_revision_change_before_token_issue(account, monkeypatch):
    _, _, lease = account
    changed = {**lease, "revision": lease["revision"] + 1}
    monkeypatch.setattr(
        "apps.infrastructure.qa_lease.get_admission_gate",
        lambda kind: SimpleNamespace(admit=lambda: changed),
    )
    with pytest.raises(AuthenticationFailed):
        _login(lease)


@pytest.mark.django_db
def test_qa_refresh_rejects_previous_revision_and_expiry_race(account):
    _, _, lease = account
    tokens = _login(lease)
    changed = {**lease, "revision": lease["revision"] + 1}
    serializer = TenantAwareTokenRefreshSerializer(
        data={"refresh": tokens["refresh"]}, context={"request": _request(changed)}
    )
    with pytest.raises(AuthenticationFailed):
        serializer.is_valid(raise_exception=True)

    draining = {**lease, "state": "draining"}
    serializer = TenantAwareTokenRefreshSerializer(
        data={"refresh": tokens["refresh"]}, context={"request": _request(draining)}
    )
    with pytest.raises(AuthenticationFailed):
        serializer.is_valid(raise_exception=True)

    near_expiry = {**lease, "expires_at": int(time.time()) + 30}
    serializer = TenantAwareTokenRefreshSerializer(
        data={"refresh": tokens["refresh"]}, context={"request": _request(near_expiry)}
    )
    with pytest.raises(AuthenticationFailed):
        serializer.is_valid(raise_exception=True)


@pytest.mark.django_db
def test_qa_refresh_rotates_without_extending_past_window(account):
    tenant, _, lease = account
    tokens = _login(lease)
    serializer = TenantAwareTokenRefreshSerializer(
        data={"refresh": tokens["refresh"]}, context={"request": _request(lease)}
    )
    serializer.is_valid(raise_exception=True)
    result = serializer.validated_data
    assert AccessToken(result["access"])["exp"] <= lease["expires_at"]
    assert AccessToken(result["access"])["tenant_id"] == tenant.id
    if "refresh" in result:
        assert RefreshToken(result["refresh"])["exp"] <= lease["expires_at"]


@pytest.mark.django_db
def test_draining_allows_bound_read_but_rejects_mutation_and_closed_read(account):
    _, _, lease = account
    tokens = _login(lease)
    prior_revision = lease["revision"]
    lease["revision"] += 1
    lease["drain_from_revision"] = prior_revision
    factory = RequestFactory()
    clear_current_tenant()
    try:
        auth = TokenVersionJWTAuthentication()
        mutation = factory.post(
            "/api/v1/exams/", HTTP_AUTHORIZATION="Bearer " + tokens["access"]
        )
        mutation.candidate_qa_lease = {**lease}
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(mutation)

        read = factory.get(
            "/api/v1/exams/results/", HTTP_AUTHORIZATION="Bearer " + tokens["access"]
        )
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(read)  # renewed active lease invalidates old revision
        lease["state"] = "draining"
        assert auth.authenticate(read)[0].is_active
        lease["state"] = "ready_for_restore"
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(read)
        lease["state"] = "draining"
        lease["expires_at"] = int(time.time()) - 1
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(read)
    finally:
        clear_current_tenant()


def test_qa_rejects_preexisting_session_authentication(monkeypatch, qa_env):
    monkeypatch.setattr(SessionAuthentication, "authenticate", lambda self, request: (object(), None))
    with pytest.raises(AuthenticationFailed):
        TenantAwareSessionAuthentication().authenticate(_request({}))


@pytest.mark.django_db
def test_real_token_route_passes_middleware_lease_to_serializer(account, monkeypatch, settings):
    tenant, _, lease = account

    class Gate:
        def admit(self):
            return lease

        @contextmanager
        def begin(self, operation_id, *, tenant_id=None, purpose=None):
            assert len(operation_id) == 32
            assert tenant_id is None and purpose == "auth"
            yield lease

    monkeypatch.setattr(admission, "_gate", lambda: Gate())
    middleware = list(settings.MIDDLEWARE)
    middleware.insert(
        middleware.index("apps.core.middleware.tenant_db_usage.TenantDatabaseUsageMiddleware") + 1,
        "apps.api.middleware.candidate_qa_admission.CandidateQaAdmissionMiddleware",
    )
    with override_settings(
        MIDDLEWARE=middleware,
        ALLOWED_HOSTS=["testserver"],
        TENANT_HEADER_CODE_ALLOWED_HOSTS=["testserver"],
    ):
        response = APIClient().post(
            "/api/v1/token/",
            {"username": "qa-teacher", "password": "qa-password-123"},
            format="json",
            HTTP_X_TENANT_CODE=tenant.code,
        )
    assert response.status_code == 200, response.content
    assert AccessToken(response.data["access"])["qa_lease_id"] == lease["lease_id"]
