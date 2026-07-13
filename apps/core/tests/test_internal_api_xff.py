"""
IsLambdaInternal 권한의 X-Forwarded-For 신뢰 경계 회귀 테스트.

검증 항목:
- TRUSTED_PROXY_CIDRS 미설정: XFF를 무시하고 직접 peer 사용.
- TRUSTED_PROXY_CIDRS 설정 + REMOTE_ADDR 가 신뢰 범위: 오른쪽부터 첫 비신뢰 hop 사용.
- TRUSTED_PROXY_CIDRS 설정 + REMOTE_ADDR 가 신뢰 범위 밖: REMOTE_ADDR 사용 (XFF 무시).
"""

from django.http import HttpRequest
from django.test import TestCase, override_settings

from apps.core.permissions import _get_client_ip


def _req(remote_addr: str, xff: str | None = None) -> HttpRequest:
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = remote_addr
    if xff is not None:
        req.META["HTTP_X_FORWARDED_FOR"] = xff
    return req


class TestGetClientIp(TestCase):

    @override_settings(TRUSTED_PROXY_CIDRS="")
    def test_xff_ignored_when_no_trusted_proxy_setting(self):
        # 신뢰 프록시가 명시되지 않으면 caller-supplied XFF를 신뢰하지 않는다.
        ip = _get_client_ip(_req("203.0.113.1", "10.0.0.5, 8.8.8.8"))
        self.assertEqual(ip, "203.0.113.1")

    @override_settings(TRUSTED_PROXY_CIDRS="172.30.0.0/16")
    def test_xff_used_when_remote_in_trusted_range(self):
        # ALB 가 직접 연결 → XFF 신뢰.
        ip = _get_client_ip(_req("172.30.5.10", "203.0.113.7, 172.30.5.10"))
        self.assertEqual(ip, "203.0.113.7")

    @override_settings(TRUSTED_PROXY_CIDRS="172.30.0.0/16")
    def test_xff_ignored_when_remote_outside_trusted_range(self):
        # 신뢰 프록시 밖에서 직접 보낸 XFF 는 위조 가능 → 무시.
        ip = _get_client_ip(_req("203.0.113.99", "10.0.0.5, 8.8.8.8"))
        self.assertEqual(ip, "203.0.113.99")

    @override_settings(TRUSTED_PROXY_CIDRS="172.30.0.0/16")
    def test_no_xff_falls_back_to_remote(self):
        ip = _get_client_ip(_req("172.30.5.10"))
        self.assertEqual(ip, "172.30.5.10")
