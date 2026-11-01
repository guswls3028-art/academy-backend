"""Execute the dev-only publisher transform without AWS or configured secrets."""
import os
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]


class OmrScoredDevelopmentEnvironmentTests(unittest.TestCase):
    def test_api_and_worker_transform_preserves_default_and_opt_in_is_development_only(self):
        # Parse only the pure transformation function; the publisher's top level
        # performs AWS mutations and must never run in this test.
        script = r"""
$ErrorActionPreference = 'Stop'
$path = Join-Path $env:OMR_ENV_TEST_ROOT 'scripts/v1/publish-api-development-env.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'publisher parse failed' }
$function = $ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Set-IsolatedDevelopmentValues'}, $true)
Invoke-Expression $function.Extent.Text
$credentialPassword = 'synthetic-test-only'
$developmentDatabaseName = 'academy_api_development'
$credentialUser = 'academy_api_development_app'
$script:ApiDevelopmentCdnSigningSecret = 'synthetic-test-only-signing'
foreach ($enabled in @($false, $true)) {
    $EnableOmrScoredReassignment = $enabled
    foreach ($module in @('apps.api.config.settings.development', 'apps.api.config.settings.worker')) {
        foreach ($initial in @($null, 'false', 'true')) {
            $source = [pscustomobject]@{DJANGO_SETTINGS_MODULE='apps.api.config.settings.prod'; DB_NAME='production'}
            if ($null -ne $initial) { $source | Add-Member OMR_SCORED_REASSIGNMENT_ENABLED $initial }
            $before = $source | ConvertTo-Json -Compress
            $target = $before | ConvertFrom-Json
            Set-IsolatedDevelopmentValues -Target $target -SettingsModule $module
            $expected = if ($enabled) { 'true' } else { [string]$initial }
            if ([string]$target.OMR_SCORED_REASSIGNMENT_ENABLED -cne $expected) { throw 'activation mismatch' }
            if (($source | ConvertTo-Json -Compress) -cne $before) { throw 'production source changed' }
            if ($target.ACADEMY_RUNTIME_ENV -ne 'development' -or $target.DB_NAME -ne 'academy_api_development' -or $target.DB_USER -ne 'academy_api_development_app' -or $target.DJANGO_SETTINGS_MODULE -ne $module) { throw 'isolation lost' }
        }
    }
}
Write-Output '12 isolated transformation cases passed'
"""
        result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "OMR_ENV_TEST_ROOT": str(ROOT)}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("12 isolated transformation cases passed", result.stdout)

    def test_exact_version_readbacks_verify_both_effective_environment_values(self):
        source = (ROOT / "scripts/v1/publish-api-development-env.ps1").read_text(encoding="utf-8")
        self.assertIn('[string]$actual.OMR_SCORED_REASSIGNMENT_ENABLED -cne [string]$production.OMR_SCORED_REASSIGNMENT_ENABLED', source)
        self.assertIn('[string]$actualWorkers.OMR_SCORED_REASSIGNMENT_ENABLED -cne [string]$productionWorkers.OMR_SCORED_REASSIGNMENT_ENABLED', source)
        self.assertIn('"$($script:ApiDevelopmentEnvParameter):$version"', source)
        self.assertIn('"$($script:ApiDevelopmentWorkersEnvParameter):$workersVersion"', source)

    def test_worker_effective_setting_is_explicit_and_defaults_off(self):
        with patch.dict(os.environ, {"SENTRY_DSN": ""}):
            os.environ.pop("OMR_SCORED_REASSIGNMENT_ENABLED", None)
            path = ROOT / "apps/api/config/settings/worker.py"
            self.assertIs(runpy.run_path(str(path))["OMR_SCORED_REASSIGNMENT_ENABLED"], False)
            for value, expected in (("true", True), ("false", False), ("1", True), ("invalid", False)):
                os.environ["OMR_SCORED_REASSIGNMENT_ENABLED"] = value
                self.assertIs(runpy.run_path(str(path))["OMR_SCORED_REASSIGNMENT_ENABLED"], expected)

    def test_normal_candidate_workflow_enables_only_isolated_development(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/v1-build-and-push-latest.yml").read_text(encoding="utf-8"))
        calls = [(job_id, step.get("run", ""))
                 for job_id, job in workflow["jobs"].items()
                 for step in job.get("steps", [])
                 if "-EnableOmrScoredReassignment" in step.get("run", "")]
        self.assertEqual(len(calls), 1)
        job_id, command = calls[0]
        self.assertEqual(job_id, "verify-api-development")
        self.assertIn("./scripts/v1/publish-api-development-env.ps1", command)
        self.assertIn('-ReleaseId "${{ env.RELEASE_IMAGE_TAG }}"', command)
        self.assertIn("-GithubOutputPath $env:GITHUB_OUTPUT", command)
        self.assertIn("-Ci", command)


if __name__ == "__main__":
    unittest.main()
