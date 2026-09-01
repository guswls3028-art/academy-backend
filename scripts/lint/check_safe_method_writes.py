from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path


SAFE_HANDLER_NAMES = frozenset({"get", "head", "list", "options", "retrieve"})
MUTATING_MANAGER_METHODS = frozenset(
    {
        "abulk_create",
        "abulk_update",
        "acreate",
        "adelete",
        "aupdate",
        "aupdate_or_create",
        "bulk_create",
        "bulk_update",
        "create",
        "delete",
        "get_or_create",
        "update",
        "update_or_create",
    }
)
MUTATING_MODEL_METHODS = frozenset({"adelete", "asave", "delete", "save", "save_base"})
CACHE_RECEIVERS = frozenset({"cache", "caches"})


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    handler: str
    call: str


class _SafeHandlerVisitor(ast.NodeVisitor):
    def __init__(self, *, path: Path):
        self.path = path
        self.handler: str | None = None
        self.violations: list[Violation] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self.handler
        if node.name in SAFE_HANDLER_NAMES:
            self.handler = node.name
            for statement in node.body:
                self.visit(statement)
        self.handler = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        if self.handler is not None:
            self.visit(node.body)

    def visit_Call(self, node: ast.Call) -> None:
        if self.handler is not None and isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value)
            method = node.func.attr
            is_manager_write = (
                method in MUTATING_MANAGER_METHODS
                and (".objects" in receiver or receiver.endswith("objects"))
            )
            is_cache_delete = method == "delete" and receiver.split(".")[-1] in CACHE_RECEIVERS
            is_model_write = method in MUTATING_MODEL_METHODS and not is_cache_delete
            is_on_commit = method == "on_commit" and receiver == "transaction"
            if is_manager_write or is_model_write or is_on_commit:
                self.violations.append(
                    Violation(
                        path=self.path,
                        line=node.lineno,
                        handler=self.handler,
                        call=f"{receiver}.{method}",
                    )
                )
        self.generic_visit(node)


def find_violations(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {"migrations", "tests", "__pycache__"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _SafeHandlerVisitor(path=path)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject direct ORM writes and on_commit callbacks in safe HTTP handlers."
    )
    parser.add_argument("root", nargs="?", default="apps", type=Path)
    args = parser.parse_args()

    violations = find_violations(args.root)
    if not violations:
        print("SAFE_METHOD_WRITE_BOUNDARY_PASS")
        return 0
    for violation in violations:
        print(
            f"{violation.path}:{violation.line}: {violation.handler} handler calls "
            f"{violation.call}"
        )
    print(f"SAFE_METHOD_WRITE_BOUNDARY_FAIL count={len(violations)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
