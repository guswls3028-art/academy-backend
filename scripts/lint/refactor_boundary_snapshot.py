"""Report refactor boundary risk counts.

This is a baseline-mode guardrail for large refactors. It does not fail by
default because the current tree has known legacy coupling. Use --strict only
after a specific baseline policy is in place.

Use --strict-touched during Phase 0 refactors. It still reports the full
baseline, but only fails when files changed in the current worktree, explicit
--touched-file paths, or a --base-ref diff contain findings.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
APPS_DOMAINS_DIR = BACKEND_DIR / "apps" / "domains"
ACADEMY_DOMAIN_DIR = BACKEND_DIR / "academy" / "domain"
ACADEMY_ADAPTERS_DIR = BACKEND_DIR / "academy" / "adapters"
SCAN_ROOTS = (APPS_DOMAINS_DIR, ACADEMY_DOMAIN_DIR, ACADEMY_ADAPTERS_DIR)

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
ADAPTER_APPLICATION_IMPORT_ALLOWLIST = {
    "academy.application.ports",
    "academy.application.video",
}


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


def is_scanned_python_file(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    try:
        path.relative_to(BACKEND_DIR)
    except ValueError:
        return False
    return any(path.is_relative_to(root) for root in SCAN_ROOTS)


def normalize_repo_path(path_text: str) -> Path | None:
    path = Path(path_text)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    path = path.resolve(strict=False)
    try:
        path.relative_to(BACKEND_DIR)
    except ValueError:
        return None
    return path


def run_git_name_only(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(BACKEND_DIR), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def collect_touched_files(
    *,
    base_ref: str | None,
    explicit_files: list[str],
    include_working_tree: bool,
) -> set[str]:
    changed: set[str] = set(explicit_files)

    if base_ref:
        changed.update(run_git_name_only(["diff", "--name-only", "--diff-filter=AM", f"{base_ref}...HEAD"]))

    if include_working_tree:
        changed.update(run_git_name_only(["diff", "--name-only", "--diff-filter=AM"]))
        changed.update(run_git_name_only(["diff", "--cached", "--name-only", "--diff-filter=AM"]))
        changed.update(run_git_name_only(["ls-files", "--others", "--exclude-standard"]))

    touched: set[str] = set()
    for item in changed:
        path = normalize_repo_path(item)
        if path and is_scanned_python_file(path):
            touched.add(rel(path))
    return touched


def findings_for_paths(findings: list[Finding], paths: set[str]) -> list[Finding]:
    return [finding for finding in findings if finding.path in paths]


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
                allowed = any(
                    module == allowed_module or module.startswith(allowed_module + ".")
                    for allowed_module in ADAPTER_APPLICATION_IMPORT_ALLOWLIST
                )
                if not allowed:
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
    parser.add_argument(
        "--strict-touched",
        action="store_true",
        help="Exit 1 only when a touched scanned Python file has findings",
    )
    parser.add_argument(
        "--base-ref",
        help="Git base ref for touched-file mode; compares BASE...HEAD",
    )
    parser.add_argument(
        "--include-working-tree",
        action="store_true",
        help="Include unstaged, staged, and untracked files in touched-file mode",
    )
    parser.add_argument(
        "--touched-file",
        action="append",
        default=[],
        help="Explicit touched file path for strict-touched mode; repeatable",
    )
    args = parser.parse_args()

    files = [path for root in SCAN_ROOTS for path in iter_py_files(root)]
    findings: list[Finding] = []
    for path in files:
        findings.extend(scan_file(path))

    summary = summarize(findings)
    touched_files: set[str] = set()
    strict_findings: list[Finding] = []
    strict_touched = args.strict_touched
    if strict_touched:
        include_working_tree = args.include_working_tree or not args.base_ref and not args.touched_file
        try:
            touched_files = collect_touched_files(
                base_ref=args.base_ref,
                explicit_files=args.touched_file,
                include_working_tree=include_working_tree,
            )
        except RuntimeError as exc:
            print(f"error: could not resolve touched files: {exc}", file=sys.stderr)
            return 2
        strict_findings = findings_for_paths(findings, touched_files)

    payload = {
        "backend": str(BACKEND_DIR),
        "files_scanned": len(files),
        "summary": summary,
        "findings": [asdict(item) for item in findings],
        "strict_touched": strict_touched,
        "touched_files": sorted(touched_files),
        "strict_summary": summarize(strict_findings),
        "strict_findings": [asdict(item) for item in strict_findings],
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
        if strict_touched:
            print(f"strict touched files: {len(touched_files)}")
            if touched_files:
                for path in sorted(touched_files):
                    print(f"  {path}")
            if strict_findings:
                print("strict touched findings:")
                for finding in strict_findings:
                    print(f"  {finding.kind} {finding.path}:{finding.line} {finding.detail}")

    if args.strict and findings:
        return 1
    if strict_touched and strict_findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
