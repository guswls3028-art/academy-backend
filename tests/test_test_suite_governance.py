from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QUALITY_GATE = REPO_ROOT / ".github" / "workflows" / "quality-gate.yml"
TEST_REQUIREMENTS = REPO_ROOT / "requirements" / "test.txt"
COVERAGE_CONFIG = REPO_ROOT / ".coveragerc"
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYTEST_CONFIG = REPO_ROOT / "pytest.ini"
APP_COVERAGE_SHARDS = {
    "api",
    "billing",
    "core",
    "domains",
    "shared",
    "support",
}
POSTGRESQL_CONTRACT_TESTS = (
    "apps/domains/fees/tests/test_payment_concurrency_pg.py",
    "apps/domains/staffs/tests/test_work_record_concurrency_pg.py",
    "apps/domains/messaging/tests/test_scheduled_dispatch_concurrency_pg.py",
    "apps/domains/results/tests/test_p0_concurrency_pg.py",
    "apps/domains/results/tests/test_score_edit_lock_concurrency_pg.py",
    "tests/test_matchup_isolation_policy_fix.py",
    "apps/domains/exams/tests/test_exam_policy_update.py",
)


def test_quality_gate_runs_the_default_collected_suite_under_coverage() -> None:
    workflow = QUALITY_GATE.read_text(encoding="utf-8")
    normalized_workflow = " ".join(workflow.replace("\\", "").split())

    assert "python -m pip install -r requirements/test.txt" in workflow
    assert workflow.count("python -m coverage run --parallel-mode") == 2
    assert (
        "python -m coverage run --parallel-mode --source=apps,academy -m pytest "
        "tests/test_smoke.py -v --tb=short -x"
    ) in normalized_workflow
    assert (
        "python -m coverage run --parallel-mode --source=apps,academy -m pytest "
        "-q --tb=short apps/api apps/billing apps/core apps/shared apps/support "
        "apps/domains tests --ignore=tests/test_smoke.py"
    ) in normalized_workflow
    assert "python -m coverage combine" in workflow
    assert (
        "python -m coverage report --fail-under=60.5 --skip-covered --show-missing"
        in workflow
    )


def test_quality_gate_runs_production_shape_postgresql_contracts() -> None:
    workflow = QUALITY_GATE.read_text(encoding="utf-8")
    normalized_workflow = " ".join(workflow.replace("\\", "").split())

    for required in (
        "postgresql-contract:",
        "image: pgvector/pgvector:0.8.2-pg16-bookworm",
        "DJANGO_SETTINGS_MODULE: apps.api.config.settings.test_pg",
        "TEST_DB_NAME: postgres",
        "DB_HOST: 127.0.0.1",
        'assert connection.vendor == "postgresql"',
        'assert database_name == "postgres"',
    ):
        assert required in workflow

    expected_command = "python -m pytest -q --tb=short " + " ".join(
        POSTGRESQL_CONTRACT_TESTS
    )
    assert expected_command in normalized_workflow


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


def test_pytest_has_one_authoritative_configuration() -> None:
    assert PYTEST_CONFIG.read_text(encoding="utf-8").startswith("[pytest]")
    assert "[tool.pytest.ini_options]" not in PYPROJECT.read_text(encoding="utf-8")


def test_every_app_test_directory_belongs_to_a_coverage_shard() -> None:
    app_test_roots = {
        path.relative_to(REPO_ROOT / "apps").parts[0]
        for path in (REPO_ROOT / "apps").glob("*/**/tests/test_*.py")
    }

    assert app_test_roots == APP_COVERAGE_SHARDS
