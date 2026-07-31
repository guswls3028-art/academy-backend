from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "v1" / "ecr-critical-scan-gate.py"
SPEC = importlib.util.spec_from_file_location("ecr_critical_scan_gate", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _finding(cve: str, package: str, version: str, severity: str = "CRITICAL") -> dict:
    return {
        "name": cve,
        "severity": severity,
        "attributes": [
            {"key": "package_name", "value": package},
            {"key": "package_version", "value": version},
        ],
    }


def _scan(*findings: dict) -> dict:
    return {"imageScanFindings": {"findings": list(findings)}}


def test_current_acceptance_is_exact_and_time_bounded() -> None:
    acceptances = gate.load_acceptances(
        Path(__file__).parents[1] / "docs" / "ssot" / "ecr-critical-risk-acceptance.json",
        date(2026, 7, 31),
    )
    accepted = gate.evaluate_findings(
        "academy-api",
        _scan(_finding("CVE-2026-5450", "glibc", "2.41-12+deb13u3")),
        acceptances,
    )
    assert len(accepted) == 1


@pytest.mark.parametrize(
    ("cve", "package", "version"),
    [
        ("CVE-2099-9999", "glibc", "2.41-12+deb13u3"),
        ("CVE-2026-5450", "glibc", "2.41-12+deb13u4"),
        ("CVE-2026-5450", "other", "2.41-12+deb13u3"),
    ],
)
def test_unknown_or_changed_critical_finding_fails_closed(
    cve: str, package: str, version: str
) -> None:
    acceptances = gate.load_acceptances(
        Path(__file__).parents[1] / "docs" / "ssot" / "ecr-critical-risk-acceptance.json",
        date(2026, 7, 31),
    )
    with pytest.raises(gate.GateError, match="unaccepted critical"):
        gate.evaluate_findings(
            "academy-api", _scan(_finding(cve, package, version)), acceptances
        )


def test_expired_acceptance_blocks_before_scanning() -> None:
    with pytest.raises(gate.GateError, match="expired"):
        gate.load_acceptances(
            Path(__file__).parents[1]
            / "docs"
            / "ssot"
            / "ecr-critical-risk-acceptance.json",
            date(2026, 8, 15),
        )


def test_high_finding_does_not_consume_critical_acceptance() -> None:
    accepted = gate.evaluate_findings(
        "academy-api", _scan(_finding("CVE-2099-9999", "demo", "1", "HIGH")), {}
    )
    assert accepted == []


def test_runtime_base_is_digest_pinned_without_unused_postgres_client() -> None:
    dockerfile = (Path(__file__).parents[1] / "docker" / "Dockerfile.base").read_text(
        encoding="utf-8"
    )
    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 2
    assert all(
        line.startswith(
            "FROM python:3.11-slim@sha256:"
            "db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93"
        )
        for line in from_lines
    )
    assert "postgresql-client" not in dockerfile


def test_missing_scan_result_is_started_then_polled(monkeypatch: pytest.MonkeyPatch) -> None:
    descriptions = iter(
        [
            {"imageScanStatus": None, "imageScanFindings": None},
            {"imageScanStatus": {"status": "COMPLETE"}, "imageScanFindings": {}},
        ]
    )
    starts: list[list[str]] = []
    monkeypatch.setattr(gate, "_describe_scan", lambda *_args: next(descriptions))
    monkeypatch.setattr(
        gate,
        "_run_aws_json",
        lambda arguments, **_kwargs: starts.append(arguments) or {},
    )

    completed = gate.wait_for_completed_scan(
        "academy-base", "sha256:" + "a" * 64, "ap-northeast-2", 2, 0
    )

    assert completed["imageScanStatus"]["status"] == "COMPLETE"
    assert starts and starts[0][:2] == ["ecr", "start-image-scan"]
