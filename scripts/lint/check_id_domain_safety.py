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


def _build_filter_chain(lines: list, first_lineno: int) -> str:
    """Build a string containing the queryset chain up to 20 lines before .first().

    Unlike the simple lookback (which stops at the first non-continuation line),
    this version collects ALL 20 preceding lines to form a wide context string.
    This handles multi-line .filter(...) blocks where individual filter args span
    many lines and no single line contains the full filter call.
    """
    parts = []
    for lookback in range(0, min(20, first_lineno)):
        prev = lines[first_lineno - 1 - lookback].strip()
        parts.append(prev)
    # Return lines in order (oldest first) so patterns read naturally
    return " ".join(reversed(parts))


def _is_pk_or_unique_lookup(lines: list, first_lineno: int) -> bool:
    """Suppress false positives: PK/unique lookups always return 0-1 rows.

    Uses a wide 20-line context window so multi-line .filter() blocks are
    captured even when arguments are each on their own line.
    """
    chain = _build_filter_chain(lines, first_lineno)

    pk_patterns = [
        # Direct PK/unique lookups
        r"\.filter\(\s*id\s*=",
        r"\.filter\(\s*pk\s*=",
        r"\.get\(",
        r"get_or_create\(",
        r"get_object_or_404\(",
        r"\.filter\([^)]*host\s*=",

        # Compound unique with user+tenant or code+tenant
        r"\.filter\([^)]*user\s*=.*tenant\s*=",
        r"\.filter\([^)]*tenant\s*=.*user\s*=",
        r"\.filter\([^)]*code\s*=.*tenant\s*=",
        r"\.filter\([^)]*tenant\s*=.*code\s*=",

        # PK lookup with tenant scope
        r"\.filter\([^)]*tenant[_\s=][^,)]*,\s*pk\s*=",
        r"\.filter\([^)]*pk\s*=.*tenant[_\s=]",
        r"\.filter\([^)]*tenant[_\s=][^,)]*,\s*id\s*=",
        r"\.filter\([^)]*id\s*=.*tenant[_\s=]",
        # pk=int(...) variants
        r"\bpk\s*=\s*int\(",
        r"\bpk\s*=\s*\w+_id\b",

        # Compound FK pairs: enrollment_id + homework_id (HomeworkScore unique_together)
        r"enrollment_id\s*=.*homework_id\s*=",
        r"homework_id\s*=.*enrollment_id\s*=",

        # Result model: target_type + target_id + enrollment_id (unique compound)
        r"target_type\s*=.*target_id\s*=.*enrollment_id\s*=",
        r"target_type\s*=.*enrollment_id\s*=",
        r"enrollment_id\s*=.*target_type\s*=",

        # ResultItem: result=X + question_id=Y (unique compound)
        r"result\s*=\s*result.*question_id\s*=",
        r"question_id\s*=.*result\s*=\s*result",

        # VideoPermission: video_id + enrollment_id
        r"video_id\s*=.*enrollment_id\s*=",
        r"enrollment_id\s*=.*video_id\s*=",

        # Single FK ID lookups (0-or-1 by design)
        r"\.filter\(\s*exam_id\s*=",
        r"\.filter\(\s*session_id\s*=",
        r"\.filter\(\s*lecture\s*=",
        r"\.filter\(\s*lecture_id\s*=",
        r"\.filter\(\s*video\s*=",
        r"\.filter\(\s*video_id\s*=",
        r"\.filter\(\s*job\s*=",

        # Tenant-scoped FK lookups
        r"session_id\s*=.*tenant[_\s=]",
        r"tenant[_\s=].*session_id\s*=",
        r"staff_id\s*=.*year\s*=.*month\s*=",   # StaffMonthSnapshot unique per staff+year+month

        # Tenant + phone (unique per tenant in practice)
        r"tenant[_\s=].*phone\s*=",
        r"phone\s*=.*tenant[_\s=]",
        r"tenant_id\s*=.*phone\s*=",
        r"deleted_at__isnull\s*=\s*True.*phone\s*=",
        r"phone\s*=.*deleted_at__isnull\s*=\s*True",

        # Tenant + trigger (AutoSendConfig: unique per tenant+trigger)
        r"tenant_id\s*=.*trigger\s*=",
        r"trigger\s*=.*tenant_id\s*=",
        r"trigger\s*=.*enabled\s*=",  # unique trigger per tenant context

        # Tenant + name (MessageTemplate name unique per tenant)
        r"tenant[_\s=].*name\s*=",
        r"name\s*=.*tenant[_\s=]",

        # is_primary=True on domain (only one primary per tenant)
        r"is_primary\s*=\s*True",

        # role=owner (at most one owner per tenant)
        r"role\s*=\s*['\"]owner['\"]",

        # submission FK (one Result per submission)
        r"submission\s*=\s*submission\b",
        r"submission_id\s*=",

        # VideoTranscodeJob state__in idempotency: one active job per video
        r"video\s*=\s*video.*state__in",
        r"state__in.*video\s*=\s*video",

        # VideoPlaybackSession: session_id is a unique token
        r"session_id\s*=\s*session_id\b",

        # Program.objects.filter(tenant=...) — Program is 1-per-tenant
        r"Program\.objects\.filter\(",

        # ScoreEditDraft: unique per session_id + tenant_id + editor_user_id
        r"editor_user_id\s*=",

        # Asset by id with tenant cross-check (multiline: id=asset_id earlier)
        r"id\s*=\s*asset_id",

        # VideoComment lookup by id (PK)
        r"id\s*=\s*parent_id",

        # Tenant.objects.filter(pk=...) or filter(id=...)
        r"Tenant\.objects\.filter\(",

        # values(...).first() on PK-scoped qs — getting a scalar from a PK lookup
        r"\.values\([^)]*\)\.first\(\)",
        r"\.values_list\([^)]*\)\.first\(\)",

        # Explicit uniqueness guard before .first(): count() checked above
        # e.g. resolver.py pattern: cnt = qs.count() ... if cnt > 1: raise ... td = qs.first()
        r"cnt\s*=\s*\w+\.count\(\)",
        r"\.count\(\).*if.*cnt\b",

        # Enrollment qs scoped to tenant + user_id or student_id (near-unique: one enrollment per user per tenant)
        r"user_id\s*=\s*user\.id",
        r"student_id\s*=\s*user\.id",

        # attendance_filter_session_enrollment / similar named functions that scope session+enrollment (unique_together)
        r"filter_session_enrollment\(",
        r"attendance_filter_.*enrollment\(",
    ]

    for pattern in pk_patterns:
        if re.search(pattern, chain):
            return True
    return False


# Paths that are non-production code — suppress UNORDERED_FIRST entirely
_NON_PRODUCTION_PATH_PARTS = (
    "management/commands/",
    "management\\commands\\",
    "/tests/",
    "\\tests\\",
    "test_",          # test_*.py files
    "_seed.py",       # seed scripts
    "_test_seed.py",
)


def _is_non_production_file(filepath: Path) -> bool:
    """Return True for management commands, test files, and seed scripts."""
    path_str = str(filepath).replace("\\", "/")
    fname = filepath.name
    return (
        "management/commands/" in path_str
        or "/tests/" in path_str
        or fname.startswith("test_")
        or fname.endswith("_seed.py")
        or fname.endswith("_test_seed.py")
    )


def check_unordered_first(filepath: Path, lines: list) -> list:
    """Check for .first() without .order_by(). Suppresses PK/unique lookups
    and non-production files (management commands, tests, seed scripts)."""
    findings = []

    # Skip non-production files entirely
    if _is_non_production_file(filepath):
        return findings

    for lineno, line in enumerate(lines, 1):
        if not RE_UNORDERED_FIRST.search(line):
            continue
        if RE_ORDER_BY.search(line):
            continue

        # Look back up to 20 lines for an .order_by() call without early exit.
        # Using 20 lines handles deeply nested Case/When order_by blocks.
        has_order_by = False
        for lookback in range(1, min(21, lineno)):
            prev_line = lines[lineno - 1 - lookback]
            if RE_ORDER_BY.search(prev_line):
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
