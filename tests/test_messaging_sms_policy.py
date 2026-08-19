from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_TOKENS = (
    "MessageType.SMS",
    "MessageType.LMS",
    '"messageType": "SMS"',
    '"messageType": "LMS"',
    "DEV_ALERTS_SMS_",
    "--test-sms",
    "--external-signal",
)


def test_runtime_and_workflows_have_no_sms_provider_dispatch() -> None:
    source_files = [
        path
        for path in (ROOT / "apps").rglob("*.py")
        if "migrations" not in path.parts and "tests" not in path.parts
    ]
    source_files.extend((ROOT / ".github" / "workflows").glob("*.yml"))
    source_files.extend((ROOT / "scripts").rglob("*.ps1"))

    violations: list[str] = []
    for path in source_files:
        source = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_RUNTIME_TOKENS:
            if token in source:
                violations.append(f"{path.relative_to(ROOT)}: {token}")

    assert violations == []


def test_alimtalk_providers_disable_sms_fallback() -> None:
    worker_source = (
        ROOT / "apps" / "worker" / "messaging_worker" / "sqs_main.py"
    ).read_text(encoding="utf-8")

    assert worker_source.count("disable_sms=True") >= 2
