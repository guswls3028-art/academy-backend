from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_snapshot_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "lint" / "refactor_boundary_snapshot.py"
    spec = importlib.util.spec_from_file_location("refactor_boundary_snapshot_for_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def configure_snapshot_roots(snapshot, tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    apps_domains = backend / "apps" / "domains"
    academy_domain = backend / "academy" / "domain"
    academy_adapters = backend / "academy" / "adapters"
    apps_domains.mkdir(parents=True)
    academy_domain.mkdir(parents=True)
    academy_adapters.mkdir(parents=True)

    monkeypatch.setattr(snapshot, "BACKEND_DIR", backend)
    monkeypatch.setattr(snapshot, "APPS_DOMAINS_DIR", apps_domains)
    monkeypatch.setattr(snapshot, "ACADEMY_DOMAIN_DIR", academy_domain)
    monkeypatch.setattr(snapshot, "ACADEMY_ADAPTERS_DIR", academy_adapters)
    monkeypatch.setattr(snapshot, "SCAN_ROOTS", (apps_domains, academy_domain, academy_adapters))
    return backend, apps_domains


def test_strict_touched_filters_findings_to_changed_files(tmp_path, monkeypatch):
    snapshot = load_snapshot_module()
    _, apps_domains = configure_snapshot_roots(snapshot, tmp_path, monkeypatch)

    changed = apps_domains / "clinic" / "views.py"
    unchanged = apps_domains / "attendance" / "views.py"
    changed.parent.mkdir()
    unchanged.parent.mkdir()
    changed.write_text("from apps.domains.students.models import Student\n", encoding="utf-8")
    unchanged.write_text("from apps.domains.lectures.models import Lecture\n", encoding="utf-8")

    findings = snapshot.scan_file(changed) + snapshot.scan_file(unchanged)
    strict_findings = snapshot.findings_for_paths(findings, {"apps/domains/clinic/views.py"})

    assert len(strict_findings) == 1
    assert strict_findings[0].path == "apps/domains/clinic/views.py"
    assert strict_findings[0].kind == "cross_domain_internal_import"


def test_strict_touched_allows_public_cross_domain_selector_import(tmp_path, monkeypatch):
    snapshot = load_snapshot_module()
    _, apps_domains = configure_snapshot_roots(snapshot, tmp_path, monkeypatch)

    changed = apps_domains / "results" / "service.py"
    changed.parent.mkdir()
    changed.write_text("from apps.domains.submissions.selectors import read_answers\n", encoding="utf-8")

    findings = snapshot.scan_file(changed)
    strict_findings = snapshot.strict_findings_for_paths(findings, {"apps/domains/results/service.py"})

    assert snapshot.summarize(findings) == {"cross_domain_import": 1}
    assert strict_findings == []


def test_collect_touched_files_keeps_only_scanned_python_paths(tmp_path, monkeypatch):
    snapshot = load_snapshot_module()
    _, apps_domains = configure_snapshot_roots(snapshot, tmp_path, monkeypatch)

    scanned = apps_domains / "clinic" / "views.py"
    migration = apps_domains / "clinic" / "migrations" / "0001_initial.py"
    docs = tmp_path / "backend" / "docs" / "note.md"
    scanned.parent.mkdir()
    migration.parent.mkdir()
    docs.parent.mkdir()
    scanned.write_text("", encoding="utf-8")
    migration.write_text("", encoding="utf-8")
    docs.write_text("", encoding="utf-8")

    touched = snapshot.collect_touched_files(
        base_ref=None,
        explicit_files=[
            "apps/domains/clinic/views.py",
            "apps/domains/clinic/migrations/0001_initial.py",
            "docs/note.md",
        ],
        include_working_tree=False,
    )

    assert touched == {"apps/domains/clinic/views.py"}


def test_current_boundary_counts_do_not_exceed_baseline():
    snapshot = load_snapshot_module()
    files = [path for root in snapshot.SCAN_ROOTS for path in snapshot.iter_py_files(root)]
    findings = []
    for path in files:
        findings.extend(snapshot.scan_file(path))

    summary = snapshot.summarize(findings)
    regressions = {
        kind: (summary.get(kind, 0), baseline)
        for kind, baseline in snapshot.BASELINE_SUMMARY.items()
        if summary.get(kind, 0) > baseline
    }

    assert not regressions, (
        "Refactor boundary baseline increased. Reduce the new coupling or, if "
        "the increase is intentional, update BASELINE_SUMMARY with the audit evidence: "
        f"{regressions}"
    )
