"""Report refactor boundary risk counts.

This is a baseline-mode guardrail for large refactors. It does not fail by
default because the current tree has known legacy coupling. Use --strict only
after a specific baseline policy is in place.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
APPS_DOMAINS_DIR = BACKEND_DIR / "apps" / "domains"
ACADEMY_DOMAIN_DIR = BACKEND_DIR / "academy" / "domain"
ACADEMY_ADAPTERS_DIR = BACKEND_DIR / "academy" / "adapters"

SKIP_PARTS = {"__pycache__", ".git", ".venv", "venv", "migrations"}
INFRA_IMPORTS = {
    "boto3",
    "botocore",
    "requests",
    "redis",
    "fitz",
    "cv2",
    "ffmpeg",
    "libs.r2_client",
    "libs.redis",
}
DOMAIN_INTERNAL_SEGMENTS = {"models", "services", "views", "api", "serializers"}


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    line: int
    detail: str


def iter_py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        files.append(path)
    return files


def module_names(node: ast.AST) -> list[tuple[str, int]]:
    names: list[tuple[str, int]] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            for alias in child.names:
                names.append((alias.name, child.lineno))
        elif isinstance(child, ast.ImportFrom) and child.module:
            names.append((child.module, child.lineno))
    return names


def rel(path: Path) -> str:
    return path.relative_to(BACKEND_DIR).as_posix()


def domain_name(path: Path) -> str | None:
    try:
        relative = path.relative_to(APPS_DOMAINS_DIR)
    except ValueError:
        return None
    if not relative.parts:
        return None
    return relative.parts[0]


def scan_file(path: Path) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [Finding("syntax_error", rel(path), exc.lineno or 0, exc.msg)]
    except UnicodeDecodeError as exc:
        return [Finding("decode_error", rel(path), 0, str(exc))]

    findings: list[Finding] = []
    source_domain = domain_name(path)
    imports = module_names(tree)

    for module, line in imports:
        if source_domain and module.startswith("apps.domains."):
            parts = module.split(".")
            target_domain = parts[2] if len(parts) > 2 else ""
            target_segment = parts[3] if len(parts) > 3 else ""
            if target_domain and target_domain != source_domain:
                kind = "cross_domain_import"
                if target_segment in DOMAIN_INTERNAL_SEGMENTS:
                    kind = "cross_domain_internal_import"
                findings.append(Finding(kind, rel(path), line, module))

        if source_domain:
            for infra in INFRA_IMPORTS:
                if module == infra or module.startswith(infra + "."):
                    findings.append(Finding("domain_infra_import", rel(path), line, module))
                    break

        if path.is_relative_to(ACADEMY_DOMAIN_DIR):
            if module == "django" or module.startswith("django."):
                findings.append(Finding("kernel_domain_django_import", rel(path), line, module))

        if path.is_relative_to(ACADEMY_ADAPTERS_DIR):
            if module == "academy.application" or module.startswith("academy.application."):
                findings.append(Finding("adapter_application_import", rel(path), line, module))

    return findings


def summarize(findings: list[Finding]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for finding in findings:
        summary[finding.kind] = summary.get(finding.kind, 0) + 1
    return dict(sorted(summary.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when any finding exists")
    args = parser.parse_args()

    files = (
        iter_py_files(APPS_DOMAINS_DIR)
        + iter_py_files(ACADEMY_DOMAIN_DIR)
        + iter_py_files(ACADEMY_ADAPTERS_DIR)
    )
    findings: list[Finding] = []
    for path in files:
        findings.extend(scan_file(path))

    summary = summarize(findings)
    payload = {
        "backend": str(BACKEND_DIR),
        "files_scanned": len(files),
        "summary": summary,
        "findings": [asdict(item) for item in findings],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Refactor boundary snapshot")
        print(f"backend: {BACKEND_DIR}")
        print(f"files_scanned: {len(files)}")
        if not summary:
            print("summary: no findings")
        else:
            print("summary:")
            for kind, count in summary.items():
                print(f"  {kind}: {count}")
        if findings:
            print("sample findings:")
            for finding in findings[:30]:
                print(f"  {finding.kind} {finding.path}:{finding.line} {finding.detail}")

    return 1 if args.strict and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
