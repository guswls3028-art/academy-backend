"""The native CI must demonstrate success, regression reproduction and repair."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    "verify_krb5", Path(__file__).parents[1] / "docker/native-security/verify-krb5.py"
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@pytest.mark.parametrize("unpatched", [False, True])
def test_requires_valid_case_and_exact_outcomes(monkeypatch, unpatched):
    seen = []

    def run(argv, **kwargs):
        case = argv[argv.index("--case") + 1]
        seen.append(case)
        assert kwargs["timeout"] == 20
        assert kwargs["env"]["KRB5_CONFIG"] == "/dev/null"
        return SimpleNamespace(returncode=-11 if unpatched and case != "valid" else 0, stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", run)
    verifier.verify(expect_unpatched=unpatched)
    assert tuple(seen) == verifier.CASES


@pytest.mark.parametrize("code", [-11, 1, 127])
def test_broken_positive_control_cannot_prove_vulnerability(monkeypatch, code):
    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code, stderr=""))
    with pytest.raises(RuntimeError, match="KRB5_valid_PROCESS"):
        verifier.verify(expect_unpatched=True)


@pytest.mark.parametrize("code", [-6, 0, 1, 127])
def test_only_real_null_crash_is_a_red_control(monkeypatch, code):
    results = iter([0, code])
    monkeypatch.setattr(
        verifier.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=next(results), stderr="")
    )
    with pytest.raises(RuntimeError, match="KRB5_extra-ticket_PROCESS"):
        verifier.verify(expect_unpatched=True)


def test_does_not_claim_unpatched_source_as_fixed(monkeypatch):
    monkeypatch.setattr(verifier.subprocess, "check_output", lambda *a, **k: "1.21.3-5+deb13u1")
    with pytest.raises(AssertionError, match="PACKAGE_Version_MISMATCH"):
        verifier.verify(package=True)
