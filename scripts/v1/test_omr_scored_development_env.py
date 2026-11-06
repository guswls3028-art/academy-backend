"""Execute the dev-only publisher transform without AWS or configured secrets."""
import base64
import copy
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts.v1.test_ssm_secure_put import install_fake_sdk, offline_environment

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

    def test_api_and_worker_stage_b_default_on_and_explicit_disable_remains_effective(self):
        with patch.dict(os.environ, {"SENTRY_DSN": ""}):
            for module in ("base.py", "worker.py"):
                path = ROOT / "apps/api/config/settings" / module
                os.environ.pop("OMR_SCORED_REASSIGNMENT_ENABLED", None)
                self.assertIs(runpy.run_path(str(path))["OMR_SCORED_REASSIGNMENT_ENABLED"], True)
                for value, expected in (("true", True), ("false", False), ("1", True),
                                        ("yes", True), ("0", False), ("off", False),
                                        ("FALSE", False), ("invalid", False), ("", False)):
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

    def test_development_publication_has_existing_constrained_sdk_before_first_put(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/v1-build-and-push-latest.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["verify-api-development"]["steps"]
        publisher = next(i for i, step in enumerate(steps) if step.get("id") == "development-env")
        setup = next(i for i, step in enumerate(steps) if str(step.get("uses", "")).startswith("actions/setup-python@"))
        install = next(i for i, step in enumerate(steps) if "pip install" in step.get("run", ""))
        self.assertLess(setup, install)
        self.assertLess(install, publisher)
        self.assertEqual(steps[setup]["with"]["python-version"], "3.11")
        self.assertEqual(steps[install]["run"],
                         "python -m pip install --constraint requirements/constraints.txt boto3 botocore")
        pins = (ROOT / "requirements/constraints.txt").read_text(encoding="utf-8").splitlines()
        for package in ("boto3", "botocore"):
            self.assertEqual(len([line for line in pins if line.startswith(package + "==")]), 1)


class RuntimeEnvironmentTransportTests(unittest.TestCase):
    def test_pair_and_compensation_keep_order_and_stdin_transport(self):
        for fault in ("", "workers-once", "compensation"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                target = Path(temp)
                (target / "core").mkdir()
                for name in ("aws.ps1", "sync_env.ps1", "candidate_env.ps1"):
                    shutil.copyfile(ROOT / "scripts/v1/core" / name, target / "core" / name)
                shutil.copyfile(ROOT / "scripts/v1/ssm_secure_put.py", target / "ssm_secure_put.py")
                install_fake_sdk(target)
                harness = target / "run.ps1"
                harness.write_text('''
$ErrorActionPreference = 'Stop'
$script:PlanMode = $false
$script:Region = 'test-region'
. (Join-Path $PSScriptRoot 'core/aws.ps1')
. (Join-Path $PSScriptRoot 'core/sync_env.ps1')
$script:SsmApiEnv = '/test/api'
$script:SsmWorkersEnv = '/test/workers'
$script:ApiEnvChanged = $true
$script:WorkersEnvChanged = $true
$script:CandidateApiEnvValue = '{"key":"candidate-forbidden-secret-marker"}'
$script:CandidateWorkersEnvValue = 'candidate-worker-forbidden-secret-marker'
$script:OriginalApiEnvValue = '{"key":"original-api-forbidden-secret-marker"}'
$script:OriginalWorkersEnvValue = 'original-worker-forbidden-secret-marker'
$script:LockChecks = 0
function Assert-DeployLockAcquired { param($Reg) $script:LockChecks += 1 }
function Write-Ok { param($message) }
function Write-Warn { param($message) }
try { Publish-RuntimeEnvCandidates; Write-Output 'PAIR_OK' }
catch { Write-Output $_.Exception.Message }
Write-Output "LOCK_CHECKS=$script:LockChecks"
''', encoding="utf-8")
                result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(harness)],
                    env=offline_environment(target, fault), capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, 0)
                self.assertNotIn("forbidden-secret-marker", result.stdout + result.stderr)
                calls = json.loads((target / "sdk-calls.json").read_text(encoding="utf-8"))
                expected = ["/test/api", "/test/workers"]
                if fault:
                    expected += ["/test/api"] + ([] if fault == "compensation" else ["/test/workers"])
                self.assertEqual([call["name"] for call in calls], expected)
                self.assertIn(f"LOCK_CHECKS={len(calls)}", result.stdout)
                self.assertEqual("PAIR_OK" in result.stdout, not fault)
                if fault == "compensation":
                    self.assertIn("rollback also failed", result.stdout)
                for call in calls:
                    self.assertIs(call["request"]["Overwrite"], True)
                    self.assertNotIn("Tier", call["request"])
                    self.assertNotIn("KeyId", call["request"])
                    self.assertNotIn(call["value"], " ".join(call["argv"]))
                    self.assertFalse(call["secret_in_environment"])
                if fault:
                    self.assertIn("original-api-", calls[2]["value"])
                if fault == "workers-once":
                    self.assertIn("original-worker-", calls[3]["value"])


class DevelopmentEnvironmentPublicationTests(unittest.TestCase):
    """Run the complete publisher and real AWS wrapper against a synthetic CLI.

    Only the identity/SSOT providers and aws executable boundary are replaced.
    No production values, profiles, credentials or AWS calls are used.
    """

    RELEASE = "sha-" + "a" * 40 + "-run-12345678901-1"
    CLI_STUB = r'''
$global:PublicationFixture = Get-Content -LiteralPath $env:PUBLICATION_FIXTURE -Raw | ConvertFrom-Json -AsHashtable
$global:PublicationCalls = [Collections.Generic.List[object]]::new()
$global:PublicationWrites = @{}
function Assert-AwsMutationIdentity {}
function global:aws {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    $global:LASTEXITCODE = 0
    if ($Arguments[0] -ne 'ssm') { throw 'Unexpected synthetic service' }
    $operation = $Arguments[1]
    $name = $Arguments[[Array]::IndexOf($Arguments, '--name') + 1]
    $fault = $global:PublicationFixture.fault
    if ($operation -eq 'put-parameter') { throw 'SecureString must not enter CLI argv' }
    if ($operation -ne 'get-parameter') { throw 'Unexpected synthetic operation' }
    $global:PublicationCalls.Add(@{operation=$operation; name=$name})
    if ($global:PublicationFixture.sources.ContainsKey($name)) {
        return (@{Parameter=@{Value=$global:PublicationFixture.sources[$name]; Version=410}} | ConvertTo-Json -Compress)
    }
    $sdkCalls = Get-Content -LiteralPath $env:SDK_CAPTURE -Raw | ConvertFrom-Json
    foreach ($call in $sdkCalls) {
        $sdkVersion = if ($call.name -eq '/test/development/workers') { 411 } else { 412 }
        $global:PublicationWrites[$call.name] = @{Value=$call.value; Version=$sdkVersion}
    }
    $parts = $name.Split(':')
    if ($parts.Count -ne 2 -or -not $global:PublicationWrites.ContainsKey($parts[0])) { throw 'Readback must select a written exact version' }
    $entry = $global:PublicationWrites[$parts[0]]
    if ([long]$parts[1] -ne $entry.Version) { throw 'Incorrect readback selector' }
    $kind = if ($parts[0] -eq '/test/development/workers') { 'workers' } else { 'api' }
    $version = $entry.Version
    $value = $entry.Value
    if ($fault -eq "$kind-version") { $version += 1 }
    if ($fault -eq "$kind-value") { $value = $value.Replace('preserved-marker', 'changed-marker') }
    if ($fault -eq "$kind-invalid") { $value = 'forbidden-secret-marker invalid JSON' }
    return (@{Parameter=@{Value=$value; Version=$version}} | ConvertTo-Json -Compress)
}
'''
    SSOT_STUB = r'''
function Load-SSOT {
    param($Env)
    $script:ApiDevelopmentEnabled = $true
    $script:ApiDevelopmentAccessMode = 'ssm-only'
    $script:Region = 'test-region'
    $script:SsmApiEnv = '/test/source/api'
    $script:SsmWorkersEnv = '/test/source/workers'
    $script:ApiDevelopmentCredentialParameter = '/test/credential/db'
    $script:ApiDevelopmentR2CredentialParameter = '/test/credential/r2'
    $script:ApiDevelopmentDatabaseName = 'academy_api_development'
    $script:ApiDevelopmentDatabaseUser = 'academy_api_development_app'
    $script:ApiDevelopmentR2BucketName = 'academy-development-artifacts'
    $script:ApiDevelopmentAiQueueName = 'academy-development-ai'
    $script:ApiDevelopmentToolsQueueName = 'academy-development-tools'
    $script:ApiDevelopmentMessagingQueueName = 'academy-development-messaging'
    $script:ApiDevelopmentEnvParameter = '/test/development/api'
    $script:ApiDevelopmentWorkersEnvParameter = '/test/development/workers'
}
'''

    @staticmethod
    def source_values():
        common = {
            "DB_NAME": "production", "DB_USER": "production_app",
            "CDN_HLS_BASE_URL": "https://cdn.hakwonplus.com",
            "CDN_HLS_SIGNING_SECRET": "synthetic-production-signing-key-123456789",
            "CDN_HLS_SIGNING_KEY_ID": "v1", "TEST_PADDING": "",
            "PRESERVED_SENTINEL": 'preserved-marker 한글 "quoted" \\slash',
            "GOOGLE_CREDENTIALS_JSON": json.dumps({"type": "synthetic", "private_key": "synthetic\\nkey"}),
            "R2_AI_BUCKET": "academy-ai", "R2_STORAGE_BUCKET": "academy-storage",
        }
        return {
            "api": {**common, "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.prod"},
            "workers": {**common, "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.worker",
                        "OPENAI_API_KEY": "synthetic-production-provider-must-be-replaced"},
        }

    @classmethod
    def publish(cls, values=None, fault="", homework=False):
        values = values or cls.source_values()
        sources = {
            "/academy/providers/development/openai-api-key": "synthetic-approved-provider",
            "/test/source/api": json.dumps(values["api"], ensure_ascii=False),
            "/test/source/workers": base64.b64encode(json.dumps(values["workers"], ensure_ascii=False).encode()).decode(),
            "/test/credential/db": json.dumps({"DB_USER": "academy_api_development_app", "DB_PASSWORD": "synthetic-only-password-12345678901234567890"}),
            "/test/credential/r2": json.dumps({"R2_ENDPOINT": "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
                "R2_REGION": "auto", "R2_ACCESS_KEY": "synthetic-only-access-key",
                "R2_SECRET_KEY": "synthetic-only-secret-key-12345678901234567890", "R2_BUCKET": "academy-development-artifacts"}),
        }
        with tempfile.TemporaryDirectory(prefix="development-publication-test-") as directory:
            target = Path(directory)
            (target / "core").mkdir()
            shutil.copyfile(ROOT / "scripts/v1/publish-api-development-env.ps1", target / "publisher.ps1")
            shutil.copyfile(ROOT / "scripts/v1/core/aws.ps1", target / "core/aws.ps1")
            shutil.copyfile(ROOT / "scripts/v1/ssm_secure_put.py", target / "ssm_secure_put.py")
            install_fake_sdk(target)
            (target / "core/env.ps1").write_text(cls.CLI_STUB, encoding="utf-8")
            (target / "core/ssot.ps1").write_text(cls.SSOT_STUB, encoding="utf-8")
            (target / "fixture.json").write_text(json.dumps({"sources": sources, "fault": fault}), encoding="utf-8")
            env = offline_environment(target, fault)
            env.update(PUBLICATION_FIXTURE=str(target / "fixture.json"),
                       PUBLICATION_ROOT=str(target), PUBLICATION_RELEASE=cls.RELEASE,
                       PUBLICATION_HOMEWORK=str(homework))
            harness = r'''
$ErrorActionPreference = 'Stop'
$exitCode = 0
try {
    & (Join-Path $env:PUBLICATION_ROOT 'publisher.ps1') -ReleaseId $env:PUBLICATION_RELEASE -EnableOmrScoredReassignment -EnableHomeworkPreview:($env:PUBLICATION_HOMEWORK -eq 'True') -Ci -GithubOutputPath (Join-Path $env:PUBLICATION_ROOT 'outputs.txt')
} catch {
    Write-Output ('PUBLICATION_ERROR=' + $_.Exception.Message)
    $exitCode = 1
} finally {
    [IO.File]::WriteAllText((Join-Path $env:PUBLICATION_ROOT 'calls.json'), (ConvertTo-Json -InputObject @($global:PublicationCalls.ToArray()) -Depth 30), [Text.UTF8Encoding]::new($false))
}
exit $exitCode
'''
            result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", harness],
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
            calls_path = target / "calls.json"
            calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
            sdk_calls = target / "sdk-calls.json"
            if sdk_calls.exists():
                calls = json.loads(sdk_calls.read_text(encoding="utf-8")) + calls
            outputs = target / "outputs.txt"
            return result, calls, outputs.read_text(encoding="utf-8") if outputs.exists() else ""

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("API_DEVELOPMENT_ENV_PUBLISHED", result.stdout)

    def test_complete_publication_preserves_values_and_exact_version_binding(self):
        result, calls, outputs = self.publish(homework=True)
        self.assert_success(result)
        puts = [call for call in calls if call["operation"] == "put-parameter"]
        self.assertEqual([call["name"] for call in puts], ["/test/development/workers", "/test/development/api"])
        worker, api = [json.loads(call["value"]) for call in puts]
        for call in puts:
            self.assertEqual(call["request"]["Tier"], "Advanced")
            self.assertIs(call["request"]["Overwrite"], True)
            self.assertNotIn(call["value"], " ".join(call["argv"]))
            self.assertFalse(call["secret_in_environment"])
            self.assertLessEqual(len(call["value"].encode("utf-8")), 8192)
        for kind, value in (("workers", worker), ("api", api)):
            for key in ("PRESERVED_SENTINEL", "GOOGLE_CREDENTIALS_JSON", "TEST_PADDING"):
                self.assertEqual(value[key], self.source_values()[kind][key])
            self.assertEqual(value["OMR_SCORED_REASSIGNMENT_ENABLED"], "true")
            self.assertEqual(value["DB_NAME"], "academy_api_development")
            self.assertEqual(value["VIDEO_BATCH_JOB_QUEUE"], "")
            self.assertEqual(value["BILLING_KEY_ENCRYPTION_PRIMARY_KEY"], "")
            self.assertEqual(value["SOLAPI_MOCK"], "true")
        self.assertEqual(worker["ACADEMY_OPENAI_API_KEY"], "synthetic-approved-provider")
        self.assertNotIn("OPENAI_API_KEY", worker)
        self.assertNotIn("EMBEDDING_OPENAI_API_KEY", worker)
        self.assertNotIn("ACADEMY_OPENAI_API_KEY", api)
        self.assertEqual(worker["HOMEWORK_PREVIEW_BATCH_JOB_DEFINITION"], "")
        self.assertRegex(api["HOMEWORK_PREVIEW_BATCH_JOB_DEFINITION"], r"^academy-development-homework-video-[a-f0-9]{12}-v411:1$")
        readbacks = [call["name"] for call in calls if call["operation"] == "get-parameter" and ":" in call["name"]]
        self.assertEqual(readbacks, ["/test/development/api:412", "/test/development/workers:411"])
        self.assertIn("workers_parameter_version=411", outputs)
        self.assertIn("parameter_version=412", outputs)

    def test_both_payloads_enforce_utf8_boundaries_before_any_write(self):
        result, calls, _ = self.publish()
        self.assert_success(result)
        baseline = {call["name"].rsplit("/", 1)[1]: len(call["value"].encode("utf-8"))
                    for call in calls if call["operation"] == "put-parameter"}
        for kind in ("workers", "api"):
            for size in (8191, 8192, 8193):
                with self.subTest(kind=kind, bytes=size):
                    values = self.source_values()
                    values[kind]["TEST_PADDING"] = "한글" + "x" * (size - baseline[kind] - 6)
                    result, calls, outputs = self.publish(values)
                    puts = [call for call in calls if call["operation"] == "put-parameter"]
                    if size <= 8192:
                        self.assert_success(result)
                        written = next(call for call in puts if call["name"].endswith("/" + kind))
                        self.assertEqual(len(written["value"].encode("utf-8")), size)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(f"DEVELOPMENT_ENV_TOO_LARGE kind={kind} bytes=8193 limit=8192", result.stdout)
                        self.assertEqual(puts, [])
                        self.assertEqual(outputs, "")
                        self.assertNotIn("한글", result.stdout + result.stderr)

    def test_homework_binding_reserves_version_digits_before_worker_write(self):
        result, calls, _ = self.publish(homework=True)
        self.assert_success(result)
        api_size = len(next(call["value"] for call in calls
                            if call["operation"] == "put-parameter" and call["name"].endswith("/api")).encode("utf-8"))
        values = self.source_values()
        # Actual three-digit version would fit, but the safe pre-write Int64
        # budget reserves another sixteen digits and must reject before either put.
        values["api"]["TEST_PADDING"] = "x" * (8192 - api_size)
        result, calls, outputs = self.publish(values, homework=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEVELOPMENT_ENV_TOO_LARGE kind=api bytes=8208", result.stdout)
        self.assertFalse(any(call["operation"] == "put-parameter" for call in calls))
        self.assertEqual(outputs, "")

    def test_failed_writes_and_readbacks_never_publish_success_or_secret_errors(self):
        for fault in ("workers-put", "api-put", "workers-no-version", "api-no-version",
                      "workers-version", "api-version", "workers-value", "api-value", "workers-invalid"):
            with self.subTest(fault=fault):
                result, calls, outputs = self.publish(fault=fault)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DEVELOPMENT_ENV_", result.stdout)
                self.assertNotIn("API_DEVELOPMENT_ENV_PUBLISHED", result.stdout)
                self.assertNotIn("forbidden-secret-marker", result.stdout + result.stderr)
                self.assertEqual(outputs, "")
                puts = [call for call in calls if call["operation"] == "put-parameter"]
                self.assertEqual(len(puts), 1 if fault in ("workers-put", "workers-no-version") else 2)

    @classmethod
    def decoder(cls):
        source = (ROOT / "scripts/v1/deploy-api-development.ps1").read_text(encoding="utf-8")
        code = "import base64,json,re,sys\n" + source.split('"import base64,json,re,sys\n', 1)[1].split('" \\\n', 1)[0]
        for placeholder, value in {
            "__RELEASE_ID__": cls.RELEASE, "__DATABASE__": "academy_api_development",
            "__TOOLS_QUEUE__": "academy-development-tools", "__AI_QUEUE__": "academy-development-ai",
        }.items():
            code = code.replace(placeholder, value)
        return code

    def test_development_bootstrap_reads_new_and_legacy_payloads_without_loss(self):
        result, calls, _ = self.publish()
        self.assert_success(result)
        raw = next(call["value"] for call in calls
                   if call["operation"] == "put-parameter" and call["name"].endswith("/workers"))
        for content in (raw.encode(), base64.b64encode(raw.encode())):
            with self.subTest(legacy=not content.startswith(b"{")):
                decoded = subprocess.run([sys.executable, "-I", "-c", self.decoder()], input=content,
                                         capture_output=True, timeout=10)
                self.assertEqual(decoded.returncode, 0, decoded.stderr)
                actual = dict(line.split("=", 1) for line in decoded.stdout.decode().splitlines())
                self.assertEqual(actual, {key: str(value) for key, value in json.loads(raw).items()})

    def test_development_bootstrap_rejects_invalid_payloads_without_values(self):
        result, calls, _ = self.publish()
        self.assert_success(result)
        valid = json.loads(next(call["value"] for call in calls
                     if call["operation"] == "put-parameter" and call["name"].endswith("/workers")))
        invalid = [b"", b'{"forbidden-secret-marker":', b"[]", b"null", b"!!!",
                   base64.b64encode(b"\xffforbidden-secret-marker"), base64.b64encode(b"[]")]
        boundary_keys = ("DJANGO_SETTINGS_MODULE", "ACADEMY_RUNTIME_ENV", "ACADEMY_DEVELOPMENT_RELEASE_ID",
                         "DB_NAME", "TOOLS_SQS_QUEUE_NAME", "AI_SQS_QUEUE_NAME_LITE",
                         "AI_SQS_QUEUE_NAME_BASIC", "AI_SQS_QUEUE_NAME_PREMIUM",
                         "VIDEO_BATCH_JOB_QUEUE", "VIDEO_BATCH_JOB_DEFINITION")
        invalid_values = [(key, "wrong-isolation-boundary") for key in boundary_keys]
        invalid_values.extend((("PRESERVED_SENTINEL", "forbidden-secret-marker\nINJECTED=1"),
                               ("bad-key", "forbidden-secret-marker")))
        for key, value in invalid_values:
            changed = copy.deepcopy(valid)
            changed[key] = value
            raw = json.dumps(changed).encode()
            invalid.extend((raw, base64.b64encode(raw)))
        invalid.append(base64.b64encode(json.dumps(valid).encode()) + b"!")
        invalid.append(json.dumps(valid).replace('"TEST_PADDING": ""', '"TEST_PADDING": "", "TEST_PADDING": ""').encode())
        invalid.append(json.dumps(valid).replace('"TEST_PADDING": ""', '"TEST_PADDING": NaN').encode())
        for content in invalid:
            with self.subTest(content_bytes=len(content)):
                decoded = subprocess.run([sys.executable, "-I", "-c", self.decoder()], input=content,
                                         capture_output=True, timeout=10)
                self.assertNotEqual(decoded.returncode, 0)
                self.assertEqual(decoded.stdout, b"")
                self.assertEqual(decoded.stderr.decode().strip(), "DEVELOPMENT_WORKERS_ENV_INVALID")


if __name__ == "__main__":
    unittest.main()
