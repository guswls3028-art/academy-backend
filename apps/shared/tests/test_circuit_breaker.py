"""Circuit breaker 단위 테스트."""
from __future__ import annotations

import time

from django.test import TestCase

from apps.shared.utils.circuit_breaker import (
    CircuitOpenError,
    circuit_breaker,
    get_circuit_state,
    reset_circuit,
)


class _Boom(Exception):
    pass


class CircuitBreakerTest(TestCase):
    def setUp(self):
        reset_circuit()

    def test_closed_normal_call_passes(self):
        @circuit_breaker(name="cb_a", failure_threshold=3, window_seconds=10, cooldown_seconds=1)
        def ok():
            return 42

        self.assertEqual(ok(), 42)
        self.assertEqual(get_circuit_state("cb_a")["state"], "closed")

    def test_failures_under_threshold_stay_closed(self):
        calls = {"n": 0}

        @circuit_breaker(name="cb_b", failure_threshold=3, window_seconds=10, cooldown_seconds=1,
                         expected_exceptions=[_Boom])
        def boom():
            calls["n"] += 1
            raise _Boom("fail")

        for _ in range(2):
            with self.assertRaises(_Boom):
                boom()
        self.assertEqual(get_circuit_state("cb_b")["state"], "closed")
        self.assertEqual(calls["n"], 2)

    def test_threshold_reached_opens_circuit(self):
        @circuit_breaker(name="cb_c", failure_threshold=3, window_seconds=10, cooldown_seconds=2,
                         expected_exceptions=[_Boom])
        def boom():
            raise _Boom("fail")

        for _ in range(3):
            with self.assertRaises(_Boom):
                boom()
        self.assertEqual(get_circuit_state("cb_c")["state"], "open")

        # 추가 호출은 즉시 CircuitOpenError
        with self.assertRaises(CircuitOpenError):
            boom()

    def test_half_open_then_close_on_success(self):
        toggle = {"fail": True}

        @circuit_breaker(name="cb_d", failure_threshold=2, window_seconds=10, cooldown_seconds=0.1,
                         expected_exceptions=[_Boom])
        def maybe():
            if toggle["fail"]:
                raise _Boom("nope")
            return "ok"

        for _ in range(2):
            with self.assertRaises(_Boom):
                maybe()
        self.assertEqual(get_circuit_state("cb_d")["state"], "open")

        # cooldown 후 성공 호출 → half_open → closed
        time.sleep(0.15)
        toggle["fail"] = False
        self.assertEqual(maybe(), "ok")
        self.assertEqual(get_circuit_state("cb_d")["state"], "closed")

    def test_half_open_re_opens_with_backoff(self):
        @circuit_breaker(name="cb_e", failure_threshold=2, window_seconds=10, cooldown_seconds=0.1,
                         expected_exceptions=[_Boom])
        def boom():
            raise _Boom("still bad")

        for _ in range(2):
            with self.assertRaises(_Boom):
                boom()
        # 첫 open
        first = get_circuit_state("cb_e")
        self.assertEqual(first["state"], "open")
        self.assertEqual(first["consecutive_open_count"], 1)

        # cooldown 후 probe 실패 → 다시 open, count 증가
        time.sleep(0.15)
        with self.assertRaises(_Boom):
            boom()
        second = get_circuit_state("cb_e")
        self.assertEqual(second["state"], "open")
        self.assertEqual(second["consecutive_open_count"], 2)

    def test_unexpected_exception_does_not_count(self):
        """expected_exceptions 외 예외는 circuit에 영향 X (코드 버그 등)."""

        @circuit_breaker(name="cb_f", failure_threshold=2, window_seconds=10, cooldown_seconds=1,
                         expected_exceptions=[_Boom])
        def bug():
            raise ValueError("코드 버그 — circuit과 무관")

        for _ in range(5):
            with self.assertRaises(ValueError):
                bug()
        self.assertEqual(get_circuit_state("cb_f")["state"], "closed")

    def test_success_clears_failure_window(self):
        toggle = {"fail": True}

        @circuit_breaker(name="cb_g", failure_threshold=3, window_seconds=10, cooldown_seconds=1,
                         expected_exceptions=[_Boom])
        def call():
            if toggle["fail"]:
                raise _Boom("x")
            return "ok"

        # 2번 실패 (3 임계 미만)
        for _ in range(2):
            with self.assertRaises(_Boom):
                call()
        # 1번 성공 → failure window 리셋
        toggle["fail"] = False
        self.assertEqual(call(), "ok")
        # 다시 실패 — 카운트 1부터 시작이므로 한 번 더는 open 안 됨
        toggle["fail"] = True
        with self.assertRaises(_Boom):
            call()
        self.assertEqual(get_circuit_state("cb_g")["state"], "closed")
