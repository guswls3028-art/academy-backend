#!/usr/bin/env python3
"""arch_guard — backend architecture boundary checker (Phase 0, baseline-frozen).

ARCHITECTURE.md §2.3 / hexagonal-cutover-policy.md 의 경계 규칙을 *빌드가 강제*하게 만드는
의존성 0(stdlib only) 정적 체커. Django 부트스트랩·DB 불필요 — ast 정적 분석만.

검사 규칙:
  cross_domain    apps/domains/<A> 가 다른 도메인 <B> 의 내부 모듈을 직접 import.
                  허용 escape hatch: `apps.domains.<B>.contracts` (목표 구조의 유일한 cross-domain 표면).
  infra_in_domain apps/domains/* 가 boto3 / redis / requests / cv2 / fitz(PyMuPDF) /
                  libs.r2_client / apps.infrastructure.storage 등 외부 인프라를 직접 import.
                  (→ academy(kernel)/adapters 경유해야 함.)

운영 방식 (사용자의 inline-style/Badge baseline 패턴과 동일):
  1) `--update-baseline` 로 현재 위반 전부를 baseline.json 에 동결(freeze).
  2) 평시 실행은 baseline 에 없는 *신규* 위반만 실패(exit 1). 기존 debt 는 통과.
  3) baseline 에 있으나 코드에서 사라진 항목 = 고쳐짐 → stale 로 보고(번다운 유도), 실패 아님.
  4) 도메인이 0 debt 에 도달하면 그 도메인을 strict 목록에 올려 영구 잠금(향후).

baseline key 는 line 번호를 제외(`rule|relpath|target`)해 파일 편집에 안 깨짐.

usage:
  python tools/arch_guard/check_boundaries.py                 # baseline 대비 검사
  python tools/arch_guard/check_boundaries.py --update-baseline
  python tools/arch_guard/check_boundaries.py --json          # 기계 판독용
  python tools/arch_guard/check_boundaries.py --root <dir>    # 검사 루트 override(self-test)
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# ── 경로 ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parents[1]  # backend/tools/arch_guard → backend/
DEFAULT_DOMAINS_ROOT = BACKEND_ROOT / "apps" / "domains"
BASELINE_PATH = SCRIPT_DIR / "baseline.json"

# ── 규칙 설정 ─────────────────────────────────────────────────────────────────
# 외부 인프라 직접 import 금지 prefix (도메인 안에서). dotted 모듈 기준.
INFRA_PREFIXES = (
    "boto3",
    "redis",
    "requests",
    "cv2",
    "fitz",          # PyMuPDF
    "pymupdf",
    "libs.r2_client",
    "apps.infrastructure.storage",
    "google.cloud.vision",
)
# cross-domain escape hatch: <other>.contracts 는 허용(목표 표면).
ALLOWED_CROSS_DOMAIN_SUFFIX = "contracts"

# 검사 제외(테스트·마이그레이션·캐시) — Phase 0 는 운영 경로 경계에 집중.
EXCLUDE_DIR_NAMES = {"migrations", "__pycache__", "tests"}


def is_excluded_file(path: Path) -> bool:
    parts = set(p.lower() for p in path.parts)
    if parts & EXCLUDE_DIR_NAMES:
        return True
    name = path.name
    if name == "tests.py" or name == "conftest.py":
        return True
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    return False


# ── 위반 모델 ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Violation:
    rule: str
    relpath: str
    line: int
    target: str
    src_domain: str

    @property
    def key(self) -> str:
        # line 제외 — 파일 편집에 안전.
        return f"{self.rule}|{self.relpath}|{self.target}"

    def human(self) -> str:
        return f"  {self.relpath}:{self.line}  [{self.src_domain}] →  {self.target}"


# ── 모듈 해석 (절대 + 상대 import) ────────────────────────────────────────────
def file_package(rel_module_path: Path) -> str:
    """파일의 __package__ (상대 import anchor) 계산. rel_module_path 는 backend 루트 기준."""
    parts = list(rel_module_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
        return ".".join(parts)
    # 일반 모듈: 마지막(파일명) 제거 → 패키지
    return ".".join(parts[:-1])


def resolve_importfrom(node: ast.ImportFrom, pkg: str) -> list[str]:
    """ImportFrom → 해석된 dotted 모듈 문자열 목록(보통 1개)."""
    if node.level == 0:
        return [node.module] if node.module else []
    # 상대 import: pkg 에서 (level-1) 만큼 위로
    base_parts = pkg.split(".") if pkg else []
    drop = node.level - 1
    if drop > 0:
        base_parts = base_parts[:-drop] if drop <= len(base_parts) else []
    anchor = ".".join(base_parts)
    if node.module:
        return [f"{anchor}.{node.module}" if anchor else node.module]
    # `from . import x, y` → anchor.x, anchor.y (도메인 판정엔 anchor 로 충분하지만 정확히)
    out = []
    for alias in node.names:
        out.append(f"{anchor}.{alias.name}" if anchor else alias.name)
    return out


def imported_modules(tree: ast.AST, pkg: str) -> list[tuple[str, int]]:
    """(dotted_module, lineno) 목록."""
    mods: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for m in resolve_importfrom(node, pkg):
                mods.append((m, node.lineno))
    return mods


# ── 위반 판정 ─────────────────────────────────────────────────────────────────
def matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def classify(module: str, src_domain: str) -> str | None:
    """이 import 가 위반이면 rule 명, 아니면 None."""
    # infra
    for p in INFRA_PREFIXES:
        if matches_prefix(module, p):
            return "infra_in_domain"
    # cross-domain
    if module.startswith("apps.domains."):
        parts = module.split(".")
        if len(parts) >= 3:
            target_domain = parts[2]
            if target_domain != src_domain:
                # escape hatch: <other>.contracts
                if len(parts) >= 4 and parts[3] == ALLOWED_CROSS_DOMAIN_SUFFIX:
                    return None
                return "cross_domain"
    return None


def scan(domains_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for py in sorted(domains_root.rglob("*.py")):
        rel_from_domains = py.relative_to(domains_root)
        if is_excluded_file(rel_from_domains):
            continue
        src_domain = rel_from_domains.parts[0]
        try:
            rel_from_backend = py.relative_to(BACKEND_ROOT)
        except ValueError:
            rel_from_backend = py  # self-test root 밖
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"WARN: parse 실패 {py}: {e}", file=sys.stderr)
            continue
        pkg = file_package(Path(*rel_from_backend.parts))
        for module, line in imported_modules(tree, pkg):
            rule = classify(module, src_domain)
            if rule:
                violations.append(
                    Violation(
                        rule=rule,
                        relpath=str(rel_from_backend).replace("\\", "/"),
                        line=line,
                        target=module,
                        src_domain=src_domain,
                    )
                )
    return violations


# ── baseline I/O ──────────────────────────────────────────────────────────────
def load_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return set(data.get("violations", []))


def write_baseline(violations: list[Violation]) -> None:
    keys = sorted({v.key for v in violations})
    by_rule: dict[str, int] = {}
    for v in violations:
        by_rule[v.rule] = by_rule.get(v.rule, 0) + 1
    payload = {
        "_comment": "arch_guard 동결 baseline. 신규 위반만 CI 차단. 항목이 줄어들면(번다운) 그대로 두지 말고 갱신.",
        "version": 1,
        "summary": by_rule,
        "count": len(keys),
        "violations": keys,
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="backend architecture boundary checker")
    ap.add_argument("--update-baseline", action="store_true", help="현재 위반을 baseline 으로 동결")
    ap.add_argument("--json", action="store_true", help="기계 판독 JSON 출력")
    ap.add_argument("--root", type=str, default=None, help="검사 루트 override(self-test용)")
    args = ap.parse_args()

    domains_root = Path(args.root).resolve() if args.root else DEFAULT_DOMAINS_ROOT
    if not domains_root.exists():
        print(f"ERROR: domains root 없음: {domains_root}", file=sys.stderr)
        return 2

    violations = scan(domains_root)
    cur_keys = {v.key for v in violations}

    if args.update_baseline:
        write_baseline(violations)
        print(f"baseline 갱신: {len(cur_keys)} 위반 동결 → {BASELINE_PATH.name}")
        for rule in sorted({v.rule for v in violations}):
            n = len([v for v in violations if v.rule == rule])
            print(f"  {rule}: {n}")
        return 0

    baseline = load_baseline()
    new = sorted(k for k in cur_keys if k not in baseline)
    stale = sorted(k for k in baseline if k not in cur_keys)
    new_violations = [v for v in violations if v.key in set(new)]

    if args.json:
        print(json.dumps({
            "current": len(cur_keys),
            "baseline": len(baseline),
            "new": new,
            "stale": stale,
        }, ensure_ascii=False, indent=2))
        return 1 if new else 0

    print(f"arch_guard: 현재 {len(cur_keys)} 위반 / baseline {len(baseline)} 동결 / "
          f"신규 {len(new)} / 해소(stale) {len(stale)}")

    if stale:
        print(f"\n✅ baseline 에서 사라진(고쳐진) 항목 {len(stale)}개 — `--update-baseline` 로 갱신 권장:")
        for k in stale[:20]:
            print(f"  - {k}")
        if len(stale) > 20:
            print(f"  ... 외 {len(stale) - 20}개")

    if new:
        print(f"\n❌ 신규 경계 위반 {len(new)}개 — 차단. (ARCHITECTURE.md §2.3)")
        by_rule: dict[str, list[Violation]] = {}
        for v in new_violations:
            by_rule.setdefault(v.rule, []).append(v)
        for rule, vs in sorted(by_rule.items()):
            print(f"\n  [{rule}] {len(vs)}건:")
            for v in sorted(vs, key=lambda x: x.relpath):
                print(v.human())
        print("\n  해결: cross_domain → 대상 도메인의 contracts.py 경유 / "
              "infra_in_domain → academy(kernel)/adapters 경유.")
        print("  (정당한 예외이고 경계 정책상 불가피하면 베이스라인 정책 재검토 후 --update-baseline.)")
        return 1

    print("\n✅ 신규 위반 없음. 통과.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
