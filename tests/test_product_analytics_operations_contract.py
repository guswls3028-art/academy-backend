from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KEY_SCRIPT = ROOT / "scripts" / "v1" / "ensure-product-analytics-hash-key.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "product-usage-maintenance.yml"
CONTROL_WORKFLOW = (
    ROOT / ".github" / "workflows" / "product-usage-pilot-controls.yml"
)
TELEMETRY_SCRIPT = ROOT / "scripts" / "v1" / "set-tenant-db-usage-telemetry.ps1"
CONTROL_SCRIPT = (
    ROOT / "scripts" / "v1" / "invoke-product-usage-pilot-control.ps1"
)
DB_SHARE_SCRIPT = (
    ROOT / "scripts" / "v1" / "read-product-analytics-db-share.ps1"
)
EXECUTABLE_CONTRACT = (
    ROOT / "scripts" / "v1" / "test-product-analytics-operations-contract.ps1"
)
QUALITY_GATE = ROOT / ".github" / "workflows" / "quality-gate.yml"


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
    db_share_script = DB_SHARE_SCRIPT.read_text(encoding="utf-8-sig")
    quality_gate = QUALITY_GATE.read_text(encoding="utf-8-sig")

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
    assert "read-product-analytics-db-share.ps1" in workflow
    assert "-GithubOutputPath $env:GITHUB_OUTPUT" in workflow
    assert "route_or_job_family like /product-analytics/" in db_share_script
    assert 'filter extra.event = "tenant_db_usage"' in db_share_script
    assert "toDouble(extra.db_duration_ms)" in db_share_script
    assert "toDouble(extra.write_query_count)" in db_share_script
    assert "extra.route_or_job_family as route_or_job_family" in db_share_script
    assert "toDouble(db_duration_ms)" not in db_share_script
    assert 'Fail-Readback "Tenant DB telemetry query timed out."' in db_share_script
    assert "test-product-analytics-operations-contract.ps1" in quality_gate
    assert EXECUTABLE_CONTRACT.is_file()


def test_pilot_controls_require_production_approval_oidc_and_exact_readback() -> None:
    workflow = CONTROL_WORKFLOW.read_text(encoding="utf-8-sig")
    script = TELEMETRY_SCRIPT.read_text(encoding="utf-8-sig")
    control_script = CONTROL_SCRIPT.read_text(encoding="utf-8-sig")

    assert "environment: production" in workflow
    assert "id-token: write" in workflow
    assert "AWS_ROLE_ARN_FOR_ECR_BUILD" in workflow
    assert "ENABLE TENANT DB TELEMETRY" in workflow
    assert "DISABLE TENANT DB TELEMETRY" in workflow
    assert "invoke-product-usage-pilot-control.ps1" in workflow
    assert "TENANT_DB_TELEMETRY_CONTROL_PASS" in workflow
    assert "-Enabled $env:ENABLED" in workflow
    assert "-SampleRate $env:SAMPLE_RATE" in workflow
    assert "set-tenant-db-usage-telemetry.ps1" in control_script
    assert "[ValidateSet(\"true\", \"false\")]" in control_script
    assert "[ValidateSet(\"0.05\", \"0.10\")]" in control_script
    assert "telemetryParameters.Disable = $true" in control_script
    assert "TelemetryScriptPath" in control_script

    assert "Assert-AwsMutationIdentity" in script
    assert "TENANT_DB_USAGE_ENABLED" in script
    assert "TENANT_DB_USAGE_SAMPLE_RATE" in script
    assert "TENANT_DB_USAGE_SLOW_REQUEST_MS" in script
    assert '"${ParameterName}:$version"' in script
    assert "TENANT_DB_TELEMETRY_READBACK_PASS" in script
    assert "Write-Host $raw" not in script
    assert "Write-Output $raw" not in script
