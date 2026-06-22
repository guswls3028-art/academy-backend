"""Fail when runtime code bypasses the submission lifecycle facade.

`apps.domains.submissions.services.transition` is the low-level state table and
guard engine. Runtime code must call the named lifecycle API instead, so new
status producers cannot silently reintroduce raw transition calls.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    BACKEND_DIR / "apps" / "domains",
    BACKEND_DIR / "apps" / "support",
    BACKEND_DIR / "apps" / "worker",
    BACKEND_DIR / "academy",
)
SKIP_PARTS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "migrations",
    "tests",
}
ALLOWED_PATHS = {
    "apps/domains/submissions/services/transition.py",
    "apps/domains/submissions/services/lifecycle.py",
}
TRANSITION_MODULE = "apps.domains.submissions.services.transition"
TRANSITION_PARENT = "apps.domains.submissions.services"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    detail: str


def rel(path: Path) -> str:
    return path.relative_to(BACKEND_DIR).as_posix()


def iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            repo_path = rel(path)
            if repo_path in ALLOWED_PATHS:
                continue
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            files.append(path)
    return files


def scan_source(path: Path, source: str) -> list[Finding]:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Finding(rel(path), exc.lineno or 0, f"syntax_error: {exc.msg}")]

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == TRANSITION_MODULE:
                    findings.append(
                        Finding(
                            rel(path),
                            node.lineno,
                            f"direct import of {TRANSITION_MODULE}",
                        )
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == TRANSITION_MODULE:
                findings.append(
                    Finding(
                        rel(path),
                        node.lineno,
                        f"direct import from {TRANSITION_MODULE}",
                    )
                )
            elif node.module == TRANSITION_PARENT:
                imported_names = {alias.name for alias in node.names}
                if "transition" in imported_names:
                    findings.append(
                        Finding(
                            rel(path),
                            node.lineno,
                            f"direct import of {TRANSITION_PARENT}.transition",
                        )
                    )
        elif isinstance(node, ast.Constant) and node.value == TRANSITION_MODULE:
            findings.append(
                Finding(
                    rel(path),
                    node.lineno,
                    f"dynamic reference to {TRANSITION_MODULE}",
                )
            )
    return findings


def main() -> int:
    findings: list[Finding] = []
    for path in iter_python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            findings.append(Finding(rel(path), 0, f"decode_error: {exc}"))
            continue
        findings.extend(scan_source(path, source))

    print("Submission lifecycle boundary guard")
    print(f"backend: {BACKEND_DIR}")
    print(f"files_scanned: {len(iter_python_files())}")
    if not findings:
        print("status: PASS")
        return 0

    print("status: FAIL")
    for finding in findings:
        print(f"  {finding.path}:{finding.line} {finding.detail}")
    print(
        "Runtime submission status changes must go through "
        "apps.domains.submissions.services.lifecycle.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
