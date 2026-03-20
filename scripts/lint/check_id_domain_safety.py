"""
ID Domain Confusion Lint Script
================================
Detects forbidden patterns that can cause ID domain confusion bugs:

1. INTEGER_FK_CANDIDATE: IntegerField/PositiveIntegerField/BigIntegerField
   with field names ending in _id that look like FK candidates.
   - NEW fields not in allowlist → exit code 1 (CI block)
   - Existing allowlisted fields → warning only

2. UNORDERED_FIRST: .first() without .order_by() on the same queryset chain.
   Non-deterministic row selection can silently return wrong entity.

3. SILENT_FALLBACK: `return None, None` in enrollment/student resolution.
   Silent failures mask ID confusion bugs.

Usage:
    python scripts/lint/check_id_domain_safety.py

Exit codes:
    0 - No new violations (warnings may be printed)
    1 - New INTEGER_FK_CANDIDATE found (not in allowlist)
"""

import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent  # backend/
APPS_DIR = BACKEND_DIR / "apps"
ALLOWLIST_FILE = SCRIPT_DIR / "integer_fk_allowlist.txt"

# Directories to skip
SKIP_DIRS = {"migrations", "__pycache__", ".git", "node_modules"}

# FK-candidate field name suffixes
FK_SUFFIXES = (
    "enrollment_id", "student_id", "tenant_id", "exam_id",
    "session_id", "lecture_id", "user_id", "submission_id",
    "question_id", "target_id", "attempt_id", "video_id",
    "lecture_id", "editor_user_id", "updated_by_user_id",
    "exam_question_id",
)

# Patterns
RE_INTEGER_FK = re.compile(
    r"^\s*(\w+_id)\s*=\s*models\."
    r"(IntegerField|PositiveIntegerField|BigIntegerField|PositiveSmallIntegerField)"
)

RE_UNORDERED_FIRST = re.compile(
    r"\.first\(\)"
)

RE_ORDER_BY = re.compile(
    r"\.order_by\("
)

RE_SILENT_FALLBACK = re.compile(
    r"return\s+None\s*,\s*None"
)

# Context patterns for SILENT_FALLBACK: only flag in enrollment/student functions
RE_ENROLLMENT_STUDENT_FUNC = re.compile(
    r"def\s+\w*(enrollment|student|enroll)\w*\s*\(", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------

def load_allowlist() -> set:
    """Load allowlisted integer FK fields from the allowlist file."""
    allowed = set()
    if not ALLOWLIST_FILE.exists():
        print(f"WARNING: Allowlist file not found: {ALLOWLIST_FILE}")
        return allowed

    with open(ALLOWLIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Format: relative_path:field_name
            allowed.add(line)
    return allowed


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_py_files() -> list:
    """Collect all .py files in apps/, excluding migrations and __pycache__."""
    files = []
    for root, dirs, filenames in os.walk(APPS_DIR):
        # Prune skipped directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(root) / fname)
    return files


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def relative_from_apps(filepath: Path) -> str:
    """Get path relative to apps/ directory."""
    try:
        return str(filepath.relative_to(APPS_DIR)).replace("\\", "/")
    except ValueError:
        return str(filepath)


def check_integer_fk(filepath: Path, lines: list, allowlist: set) -> list:
    """Check for IntegerField fields that look like FK candidates."""
    findings = []
    rel_path = relative_from_apps(filepath)

    for lineno, line in enumerate(lines, 1):
        match = RE_INTEGER_FK.match(line)
        if not match:
            continue

        field_name = match.group(1)
        field_type = match.group(2)

        # Only flag fields that end with known FK suffixes
        if not any(field_name == suffix or field_name.endswith("_" + suffix)
                   for suffix in FK_SUFFIXES):
            # Also catch any _id field as a general warning
            if not field_name.endswith("_id"):
                continue

        allowlist_key = f"{rel_path}:{field_name}"
        is_allowed = allowlist_key in allowlist

        findings.append({
            "file": str(filepath),
            "line": lineno,
            "pattern": "INTEGER_FK_CANDIDATE",
            "field": field_name,
            "type": field_type,
            "allowed": is_allowed,
            "description": (
                f"{field_name} = models.{field_type}(...) "
                f"{'[ALLOWED]' if is_allowed else '[NEW - NOT IN ALLOWLIST]'}"
            ),
        })

    return findings


def _is_pk_or_unique_lookup(lines: list, first_lineno: int) -> bool:
    """Suppress false positives: PK/unique lookups always return 0-1 rows."""
    chain = ""
    for lookback in range(0, min(15, first_lineno)):
        prev = lines[first_lineno - 1 - lookback].strip()
        chain = prev + " " + chain
        if not prev.startswith(".") and not prev.startswith(")") and lookback > 0:
            break
    pk_patterns = [
        r"\.filter\(\s*id\s*=", r"\.filter\(\s*pk\s*=",
        r"\.get\(", r"get_or_create\(", r"get_object_or_404\(",
        r"\.filter\([^)]*user\s*=.*tenant\s*=",
        r"\.filter\([^)]*tenant\s*=.*user\s*=",
        r"\.filter\([^)]*code\s*=.*tenant\s*=",
        r"\.filter\([^)]*tenant\s*=.*code\s*=",
        r"\.filter\([^)]*host\s*=",
    ]
    for pattern in pk_patterns:
        if re.search(pattern, chain):
            return True
    return False


def check_unordered_first(filepath: Path, lines: list) -> list:
    """Check for .first() without .order_by(). Suppresses PK/unique lookups."""
    findings = []

    for lineno, line in enumerate(lines, 1):
        if not RE_UNORDERED_FIRST.search(line):
            continue
        if RE_ORDER_BY.search(line):
            continue

        has_order_by = False
        for lookback in range(1, min(11, lineno)):
            prev_line = lines[lineno - 1 - lookback]
            if RE_ORDER_BY.search(prev_line):
                has_order_by = True
                break
            stripped = prev_line.strip()
            if stripped and not stripped.startswith(".") and not stripped.startswith(")"):
                if RE_ORDER_BY.search(stripped):
                    has_order_by = True
                break

        if has_order_by:
            continue
        if _is_pk_or_unique_lookup(lines, lineno):
            continue

        findings.append({
            "file": str(filepath),
            "line": lineno,
            "pattern": "UNORDERED_FIRST",
            "allowed": True,
            "description": ".first() without .order_by() - non-deterministic row selection",
        })

    return findings


def check_silent_fallback(filepath: Path, lines: list) -> list:
    """Check for `return None, None` in enrollment/student resolution functions."""
    findings = []
    current_func = None
    current_func_line = 0

    for lineno, line in enumerate(lines, 1):
        # Track current function
        func_match = re.match(r"\s*def\s+(\w+)\s*\(", line)
        if func_match:
            current_func = func_match.group(1)
            current_func_line = lineno

        # Check for return None, None
        if RE_SILENT_FALLBACK.search(line):
            # Only flag if we're inside an enrollment/student function
            if current_func and RE_ENROLLMENT_STUDENT_FUNC.search(
                f"def {current_func}("
            ):
                findings.append({
                    "file": str(filepath),
                    "line": lineno,
                    "pattern": "SILENT_FALLBACK",
                    "allowed": True,  # warning only
                    "description": (
                        f"return None, None in {current_func}() "
                        f"- silent failure masks ID confusion"
                    ),
                })

    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    allowlist = load_allowlist()
    py_files = collect_py_files()

    all_findings = []
    new_violations = []

    for filepath in sorted(py_files):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except (IOError, OSError) as e:
            print(f"WARNING: Could not read {filepath}: {e}", file=sys.stderr)
            continue

        findings = []
        findings.extend(check_integer_fk(filepath, lines, allowlist))
        findings.extend(check_unordered_first(filepath, lines))
        findings.extend(check_silent_fallback(filepath, lines))

        all_findings.extend(findings)

        for f in findings:
            if not f["allowed"]:
                new_violations.append(f)

    # ---------------------------------------------------------------------------
    # Output
    # ---------------------------------------------------------------------------

    if not all_findings:
        print("OK: No ID domain confusion patterns found.")
        return 0

    # Print all findings
    warning_count = 0
    error_count = 0

    for f in all_findings:
        level = "WARNING" if f["allowed"] else "ERROR"
        if f["allowed"]:
            warning_count += 1
        else:
            error_count += 1

        print(f"{f['file']}:{f['line']}: {level}: {f['pattern']}: {f['description']}")

    # Summary
    print()
    print(f"Summary: {warning_count} warning(s), {error_count} error(s)")

    if new_violations:
        print()
        print("=" * 70)
        print("BLOCKED: New INTEGER_FK_CANDIDATE fields detected!")
        print("These fields use IntegerField instead of ForeignKey.")
        print("Either:")
        print("  1. Convert to ForeignKey (preferred)")
        print("  2. Add to scripts/lint/integer_fk_allowlist.txt with approval")
        print("=" * 70)
        for v in new_violations:
            print(f"  - {v['file']}:{v['line']}: {v['description']}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
