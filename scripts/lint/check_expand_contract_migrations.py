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


def _constant_keyword(call: ast.Call, name: str):
    value = next(
        (keyword.value for keyword in call.keywords if keyword.arg == name),
        None,
    )
    if isinstance(value, ast.Constant):
        return value.value
    return None


def _field_signature_without_max_length(call: ast.Call) -> str:
    normalized = ast.Call(
        func=call.func,
        args=call.args,
        keywords=[
            keyword for keyword in call.keywords if keyword.arg != "max_length"
        ],
    )
    return ast.dump(normalized, annotate_fields=True, include_attributes=False)


def _is_safe_charfield_widening(before: ast.Call, after: ast.Call) -> bool:
    def field_kind(call: ast.Call) -> str | None:
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "models"
        ):
            return func.attr
        return None

    before_length = _constant_keyword(before, "max_length")
    after_length = _constant_keyword(after, "max_length")
    return (
        field_kind(before) == "CharField"
        and field_kind(after) == "CharField"
        and isinstance(before_length, int)
        and isinstance(after_length, int)
        and after_length > before_length
        and _field_signature_without_max_length(before)
        == _field_signature_without_max_length(after)
    )


def _operation_field_call(
    entry: ast.Call,
    *,
    model_name: str,
    field_name: str,
) -> ast.Call | None:
    operation = entry.func
    if not (
        isinstance(operation, ast.Attribute)
        and isinstance(operation.value, ast.Name)
        and operation.value.id == "migrations"
    ):
        return None
    keywords = {keyword.arg: keyword.value for keyword in entry.keywords}
    if operation.attr in {"AddField", "AlterField"}:
        candidate_model = keywords.get("model_name")
        candidate_name = keywords.get("name")
        field = keywords.get("field")
        if (
            isinstance(candidate_model, ast.Constant)
            and str(candidate_model.value).lower() == model_name.lower()
            and isinstance(candidate_name, ast.Constant)
            and str(candidate_name.value) == field_name
            and isinstance(field, ast.Call)
        ):
            return field
        return None
    if operation.attr != "CreateModel":
        return None
    candidate_model = keywords.get("name")
    fields = keywords.get("fields")
    if not (
        isinstance(candidate_model, ast.Constant)
        and str(candidate_model.value).lower() == model_name.lower()
        and isinstance(fields, (ast.List, ast.Tuple))
    ):
        return None
    for item in fields.elts:
        if not isinstance(item, (ast.List, ast.Tuple)) or len(item.elts) != 2:
            continue
        candidate_name, field = item.elts
        if (
            isinstance(candidate_name, ast.Constant)
            and str(candidate_name.value) == field_name
            and isinstance(field, ast.Call)
        ):
            return field
    return None


def _alter_field_calls(tree: ast.Module) -> dict[tuple[str, str], ast.Call]:
    migration = _migration_class(tree)
    operations = _class_assignment(migration, "operations")
    if not isinstance(operations, (ast.List, ast.Tuple)):
        return {}
    calls: dict[tuple[str, str], ast.Call] = {}
    for entry in operations.elts:
        if not isinstance(entry, ast.Call):
            continue
        operation = entry.func
        if not (isinstance(operation, ast.Attribute) and operation.attr == "AlterField"):
            continue
        keywords = {keyword.arg: keyword.value for keyword in entry.keywords}
        model = keywords.get("model_name")
        name = keywords.get("name")
        field = keywords.get("field")
        if (
            isinstance(model, ast.Constant)
            and isinstance(name, ast.Constant)
            and isinstance(field, ast.Call)
        ):
            calls[(str(model.value).lower(), str(name.value))] = field
    return calls


def _same_app_dependency(tree: ast.Module, app_label: str) -> str | None:
    migration = _migration_class(tree)
    dependencies = _class_assignment(migration, "dependencies")
    if not isinstance(dependencies, (ast.List, ast.Tuple)):
        return None
    for item in dependencies.elts:
        if not isinstance(item, (ast.List, ast.Tuple)) or len(item.elts) != 2:
            continue
        app, name = item.elts
        if (
            isinstance(app, ast.Constant)
            and app.value == app_label
            and isinstance(name, ast.Constant)
            and isinstance(name.value, str)
        ):
            return name.value
    return None


def _previous_field_call(
    repo: Path,
    migration_path: str,
    tree: ast.Module,
    *,
    model_name: str,
    field_name: str,
) -> ast.Call | None:
    migration_dir = (repo / migration_path).parent
    app_label = migration_dir.parent.name
    dependency = _same_app_dependency(tree, app_label)
    visited: set[str] = set()
    while dependency and dependency not in visited:
        visited.add(dependency)
        dependency_path = migration_dir / f"{dependency}.py"
        if not dependency_path.is_file():
            return None
        dependency_tree = ast.parse(
            dependency_path.read_text(encoding="utf-8"),
            filename=str(dependency_path),
        )
        migration = _migration_class(dependency_tree)
        operations = _class_assignment(migration, "operations")
        if isinstance(operations, (ast.List, ast.Tuple)):
            for entry in reversed(operations.elts):
                if not isinstance(entry, ast.Call):
                    continue
                field = _operation_field_call(
                    entry,
                    model_name=model_name,
                    field_name=field_name,
                )
                if field is not None:
                    return field
        dependency = _same_app_dependency(dependency_tree, app_label)
    return None


def _safe_charfield_widenings(
    repo: Path,
    migration_path: str,
    tree: ast.Module,
) -> set[tuple[str, str]]:
    safe: set[tuple[str, str]] = set()
    for (model_name, field_name), after in _alter_field_calls(tree).items():
        before = _previous_field_call(
            repo,
            migration_path,
            tree,
            model_name=model_name,
            field_name=field_name,
        )
        if before is not None and _is_safe_charfield_widening(before, after):
            safe.add((model_name, field_name))
    return safe


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
    safe_alter_fields: set[tuple[str, str]] | None = None,
) -> list[Finding]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [Finding(path, f"migration syntax is invalid: {exc}")]

    operations = set(_operation_names(tree))
    if _has_non_nullable_add_field(tree):
        operations.add("AddField(non-null without db_default)")
    if "AlterField" in operations:
        alter_fields = set(_alter_field_calls(tree))
        if alter_fields and alter_fields <= (safe_alter_fields or set()):
            operations.remove("AlterField")
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
                "contract migration is blocked in automatic push deploys; PR review "
                "may use --allow-contract-review, while execution requires an explicit "
                "workflow_dispatch with allow_contract_migrations=true only after the "
                "expand release is fully deployed or compatibility is otherwise proven",
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
            tree = ast.parse(after, filename=path)
            findings.extend(
                inspect_new_migration(
                    path,
                    after,
                    allow_contract=allow_contract,
                    safe_alter_fields=_safe_charfield_widenings(repo, path, tree),
                )
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
    parser.add_argument("--allow-contract-review", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    findings = run(
        repo,
        args.base_ref,
        allow_contract=args.allow_contract or args.allow_contract_review,
    )
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
