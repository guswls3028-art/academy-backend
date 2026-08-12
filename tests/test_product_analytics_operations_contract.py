from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KEY_SCRIPT = ROOT / "scripts" / "v1" / "ensure-product-analytics-hash-key.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "product-usage-maintenance.yml"
CONTROL_WORKFLOW = (
    ROOT / ".github" / "workflows" / "product-usage-pilot-controls.yml"
)
TELEMETRY_SCRIPT = ROOT / "scripts" / "v1" / "set-tenant-db-usage-telemetry.ps1"


def test_hash_key_script_preserves_secure_parameter_and_never_prints_key() -> None:
    script = KEY_SCRIPT.read_text(encoding="utf-8-sig")

    assert "Assert-AwsMutationIdentity" in script
    assert "RandomNumberGenerator" in script
    assert '"SecureString"' in script
    assert '"${ParameterName}:$version"' in script
    assert "configured=true" in script
    assert "Write-Host $actualKey" not in script
    assert "Write-Output $actualKey" not in script


def test_daily_maintenance_is_oidc_only_fail_closed_and_scope_limited() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8-sig")

    assert "id-token: write" in workflow
    assert "AWS_ROLE_ARN_FOR_ECR_BUILD" in workflow
    assert "academy-v1-api-asg" in workflow
    assert "HealthStatus==`Healthy`" in workflow
    assert "rollup_product_usage --date" in workflow
    assert "purge_product_usage --before" in workflow
    assert "--daily-before" in workflow
    assert "--execute" in workflow
    assert "Product analytics maintenance failed" in workflow
    assert "PRODUCT_ANALYTICS_MAINTENANCE_PASS" in workflow
    assert "report_product_usage_pilot --tenant-code hakwonplus" in workflow
    assert "--disable-on-hard-breach" in workflow
    assert "DISABLE hakwonplus ON HARD BREACH" in workflow
    assert "product-usage-pilot-report-${{ github.run_id }}" in workflow
    assert "retention-days: 90" in workflow
    assert "route_or_job_family like /product-analytics/" in workflow
    assert 'filter extra.event = "tenant_db_usage"' in workflow
    assert "toDouble(extra.db_duration_ms)" in workflow
    assert "toDouble(extra.write_query_count)" in workflow
    assert "extra.route_or_job_family as route_or_job_family" in workflow
    assert "toDouble(db_duration_ms)" not in workflow


def test_pilot_controls_require_production_approval_oidc_and_exact_readback() -> None:
    workflow = CONTROL_WORKFLOW.read_text(encoding="utf-8-sig")
    script = TELEMETRY_SCRIPT.read_text(encoding="utf-8-sig")

    assert "environment: production" in workflow
    assert "id-token: write" in workflow
    assert "AWS_ROLE_ARN_FOR_ECR_BUILD" in workflow
    assert "ENABLE TENANT DB TELEMETRY" in workflow
    assert "DISABLE TENANT DB TELEMETRY" in workflow
    assert "set-tenant-db-usage-telemetry.ps1" in workflow
    assert "TENANT_DB_TELEMETRY_CONTROL_PASS" in workflow
    assert '"-Ci"' not in workflow
    assert '-SampleRate $sampleRate' in workflow
    assert '-Disable' in workflow

    assert "Assert-AwsMutationIdentity" in script
    assert "TENANT_DB_USAGE_ENABLED" in script
    assert "TENANT_DB_USAGE_SAMPLE_RATE" in script
    assert "TENANT_DB_USAGE_SLOW_REQUEST_MS" in script
    assert '"${ParameterName}:$version"' in script
    assert "TENANT_DB_TELEMETRY_READBACK_PASS" in script
    assert "Write-Host $raw" not in script
    assert "Write-Output $raw" not in script
