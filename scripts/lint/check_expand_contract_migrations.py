#!/usr/bin/env python3
"""Fail closed on migration changes that violate zero-downtime expand/contract."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MIGRATION_PARTS = {"migrations"}
CONTRACT_OPERATIONS = {
    "AddConstraint",
    "AddIndex",
    "AlterField",
    "AlterIndexTogether",
    "AlterModelTable",
    "AlterOrderWithRespectTo",
    "AlterUniqueTogether",
    "DeleteModel",
    "RemoveField",
    "RemoveConstraint",
    "RemoveIndex",
    "RenameField",
    "RenameModel",
    "RunSQL",
    "SeparateDatabaseAndState",
}


@dataclass(frozen=True)
class Finding:
    path: str
    message: str


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def _is_migration(path: str) -> bool:
    parts = Path(path).parts
    return (
        MIGRATION_PARTS.issubset(parts)
        and path.endswith(".py")
        and Path(path).name != "__init__.py"
    )


def _changed_migrations(repo: Path, base_ref: str) -> list[tuple[str, str]]:
    changed: dict[str, str] = {}
    diff = _git(
        repo,
        "diff",
        "--name-status",
        "--find-renames",
        f"{base_ref}...HEAD",
        "--",
        "apps/**/migrations/*.py",
    ).stdout
    for raw in diff.splitlines():
        fields = raw.split("\t")
        if len(fields) < 2:
            continue
        status = fields[0]
        path = fields[-1].replace("\\", "/")
        if _is_migration(path):
            changed[path] = status[0]

    # Local runs must also cover staged, unstaged, and untracked migrations.
    status_output = _git(
        repo,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        "apps/**/migrations/*.py",
    ).stdout
    for raw in status_output.splitlines():
        if len(raw) < 4:
            continue
        code = raw[:2]
        path = raw[3:].split(" -> ")[-1].replace("\\", "/")
        if not _is_migration(path):
            continue
        changed[path] = "A" if code == "??" or "A" in code else "M"
    return sorted(changed.items())


def _assignment_value(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        return None
    return None


def _migration_class(tree: ast.Module) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            return node
    return None


def _class_assignment(node: ast.ClassDef | None, name: str) -> ast.AST | None:
    if node is None:
        return None
    for item in node.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return item.value
    return None


def _operation_names(tree: ast.Module) -> list[str]:
    migration = _migration_class(tree)
    operations = _class_assignment(migration, "operations")
    if not isinstance(operations, (ast.List, ast.Tuple)):
        return []
    names: list[str] = []
    for entry in operations.elts:
        if not isinstance(entry, ast.Call):
            continue
        func = entry.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "migrations"
        ):
            if func.attr == "SeparateDatabaseAndState":
                database_operations = next(
                    (
                        keyword.value
                        for keyword in entry.keywords
                        if keyword.arg == "database_operations"
                    ),
                    None,
                )
                if isinstance(database_operations, (ast.List, ast.Tuple)) and not (
                    database_operations.elts
                ):
                    continue
            names.append(func.attr)
    return names


def _has_non_nullable_add_field(tree: ast.Module) -> bool:
    migration = _migration_class(tree)
    operations = _class_assignment(migration, "operations")
    if not isinstance(operations, (ast.List, ast.Tuple)):
        return False
    for entry in operations.elts:
        if not (
            isinstance(entry, ast.Call)
            and isinstance(entry.func, ast.Attribute)
            and entry.func.attr == "AddField"
        ):
            continue
        field_node = next(
            (keyword.value for keyword in entry.keywords if keyword.arg == "field"),
            None,
        )
        if not isinstance(field_node, ast.Call):
            return True
        keywords = {keyword.arg: keyword.value for keyword in field_node.keywords}
        null_node = keywords.get("null")
        null_is_true = isinstance(null_node, ast.Constant) and null_node.value is True
        has_db_default = "db_default" in keywords
        if not null_is_true and not has_db_default:
            return True
    return False


def _migration_semantics(source: str) -> str:
    tree = ast.parse(source)
    migration = _migration_class(tree)
    payload = ast.Module(
        body=[
            ast.Expr(
                value=_class_assignment(migration, "dependencies")
                or ast.Constant(None)
            ),
            ast.Expr(
                value=_class_assignment(migration, "operations")
                or ast.Constant(None)
            ),
        ],
        type_ignores=[],
    )
    return ast.dump(payload, annotate_fields=True, include_attributes=False)


def inspect_new_migration(
    path: str,
    source: str,
    *,
    allow_contract: bool,
) -> list[Finding]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [Finding(path, f"migration syntax is invalid: {exc}")]

    operations = set(_operation_names(tree))
    if _has_non_nullable_add_field(tree):
        operations.add("AddField(non-null without db_default)")
    unsafe = sorted(operations & CONTRACT_OPERATIONS)
    if "AddField(non-null without db_default)" in operations:
        unsafe.append("AddField(non-null without db_default)")
    if not unsafe:
        return []

    phase = _assignment_value(tree, "ACADEMY_MIGRATION_PHASE")
    reason = _assignment_value(tree, "ACADEMY_MIGRATION_REASON")
    if phase != "contract" or not isinstance(reason, str) or len(reason.strip()) < 20:
        return [
            Finding(
                path,
                "backward-incompatible operations "
                f"{', '.join(unsafe)} require ACADEMY_MIGRATION_PHASE = "
                "'contract' and a specific ACADEMY_MIGRATION_REASON (20+ chars)",
            )
        ]
    if not allow_contract:
        return [
            Finding(
                path,
                "contract migration is blocked in automatic push deploys; run an "
                "explicit workflow_dispatch with allow_contract_migrations=true "
                "only after the expand release is fully deployed",
            )
        ]
    return []


def inspect_modified_migration(path: str, before: str, after: str) -> list[Finding]:
    try:
        if _migration_semantics(before) == _migration_semantics(after):
            return []
    except SyntaxError as exc:
        return [Finding(path, f"migration syntax is invalid: {exc}")]
    return [
        Finding(
            path,
            "an existing migration's dependencies or operations changed; preserve "
            "applied migration history and add a new migration instead",
        )
    ]


def run(repo: Path, base_ref: str, *, allow_contract: bool) -> list[Finding]:
    findings: list[Finding] = []
    for path, status in _changed_migrations(repo, base_ref):
        target = repo / path
        if status == "D":
            findings.append(Finding(path, "deleting an existing migration is prohibited"))
            continue
        if not target.is_file():
            findings.append(Finding(path, "changed migration file is missing"))
            continue
        after = target.read_text(encoding="utf-8")
        before_result = _git(repo, "show", f"{base_ref}:{path}", check=False)
        if status == "A" or before_result.returncode != 0:
            findings.extend(
                inspect_new_migration(path, after, allow_contract=allow_contract)
            )
        else:
            findings.extend(
                inspect_modified_migration(path, before_result.stdout, after)
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", default="HEAD^")
    parser.add_argument("--allow-contract", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    findings = run(repo, args.base_ref, allow_contract=args.allow_contract)
    if findings:
        for finding in findings:
            print(
                f"EXPAND_CONTRACT_FAIL {finding.path}: {finding.message}",
                file=sys.stderr,
            )
        return 1
    print("EXPAND_CONTRACT_PASS changed migrations are backward-compatible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
