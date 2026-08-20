import ast
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
    '"sms_allowed"',
)
FORBIDDEN_RUNTIME_SYMBOLS = {
    "enqueue_sms",
    "send_sms",
    "send_ppurio_sms",
    "send_one_sms",
    "send_one_sms_own_solapi",
    "send_one_sms_ppurio",
    "SmsEndpointThrottle",
}


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


def test_runtime_has_no_sms_callable_surface() -> None:
    violations: list[str] = []
    for path in (ROOT / "apps").rglob("*.py"):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in FORBIDDEN_RUNTIME_SYMBOLS:
                    violations.append(f"{path.relative_to(ROOT)}: {node.name}")

    assert violations == []


def test_public_messaging_services_export_alimtalk_only() -> None:
    from apps.domains.messaging import services

    assert hasattr(services, "enqueue_alimtalk")
    assert not hasattr(services, "enqueue_sms")
    assert not hasattr(services, "send_sms")


def test_alimtalk_providers_disable_sms_fallback() -> None:
    worker_source = (
        ROOT / "apps" / "worker" / "messaging_worker" / "sqs_main.py"
    ).read_text(encoding="utf-8")

    assert worker_source.count("disable_sms=True") >= 2
