from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QUALITY_GATE = REPO_ROOT / ".github" / "workflows" / "quality-gate.yml"
TEST_REQUIREMENTS = REPO_ROOT / "requirements" / "test.txt"
COVERAGE_CONFIG = REPO_ROOT / ".coveragerc"
APP_COVERAGE_SHARDS = {
    "api",
    "billing",
    "core",
    "domains",
    "shared",
    "support",
}


def test_quality_gate_runs_the_default_collected_suite_under_coverage() -> None:
    workflow = QUALITY_GATE.read_text(encoding="utf-8")

    assert "python -m pip install -r requirements/test.txt" in workflow
    assert workflow.count("python -m coverage run --parallel-mode") == 3
    assert "apps/api apps/billing apps/core apps/shared apps/support" in workflow
    assert "--tb=short apps/domains" in workflow
    assert "--tb=short tests" in workflow
    assert "python -m coverage combine" in workflow
    assert (
        "python -m coverage report --fail-under=60.5 --skip-covered --show-missing"
        in workflow
    )


def test_test_manifest_covers_runner_api_and_pdf_fixture_imports() -> None:
    requirements = TEST_REQUIREMENTS.read_text(encoding="utf-8")

    for required in (
        "-r ./requirements.txt",
        "-r ./api.txt",
        "pytest==",
        "pytest-django==",
        "coverage==",
        "pdfplumber==",
        "pdf2image==",
    ):
        assert required in requirements


def test_coverage_report_excludes_tests_and_migrations() -> None:
    coverage_config = COVERAGE_CONFIG.read_text(encoding="utf-8")

    for omitted in ("*/migrations/*", "*/tests/*", "*/tests.py", "*/test_*.py"):
        assert omitted in coverage_config


def test_every_app_test_directory_belongs_to_a_coverage_shard() -> None:
    app_test_roots = {
        path.relative_to(REPO_ROOT / "apps").parts[0]
        for path in (REPO_ROOT / "apps").glob("*/**/tests/test_*.py")
    }

    assert app_test_roots == APP_COVERAGE_SHARDS
