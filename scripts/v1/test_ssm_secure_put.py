"""Offline tests of the real stdin bridge and PowerShell process boundary."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.v1 import ssm_secure_put as bridge


ROOT = Path(__file__).resolve().parents[2]
SENTINEL = 'forbidden-secret-marker 한글 "quoted" \\slash\nline'

# Imported only in synthetic subprocesses, never into a configured AWS client.
SDK_STUB = '''
import json, os, pathlib, sys
class Session:
    def __init__(self, **kwargs): self.options = kwargs
    def client(self, service, **kwargs):
        assert service == 'ssm'
        return self
    def put_parameter(self, **request):
        capture = pathlib.Path(os.environ['SDK_CAPTURE'])
        calls = json.loads(capture.read_text()) if capture.exists() else []
        kind = 'workers' if request['Name'].endswith('workers') else 'api'
        fault = os.environ.get('SDK_FAULT', '')
        calls.append({'operation':'put-parameter', 'name':request['Name'],
                      'value':request['Value'], 'request':request, 'argv':sys.argv,
                      'session':self.options,
                      'secret_in_environment': any(request['Value'] in v for v in os.environ.values())})
        capture.write_text(json.dumps(calls), encoding='utf-8')
        is_original = 'original-' in request['Value']
        paired_failure = fault in ('workers-once', 'compensation') and kind == 'workers' and not is_original
        compensation_failure = fault == 'compensation' and is_original
        if fault in ('raise', kind + '-put') or paired_failure or compensation_failure:
            print(request['Value'])
            print(request['Value'], file=sys.stderr)
            raise RuntimeError(request['Value'])
        if fault in ('no-version', kind + '-no-version'): return {}
        if fault == 'noise':
            print(request['Value'])
            print(request['Value'], file=sys.stderr)
        version = 411 if kind == 'workers' else 412
        return {'Version':version, 'Tier':'Advanced', 'Value':request['Value']}
'''


def install_fake_sdk(directory: Path):
    (directory / "boto3.py").write_text(SDK_STUB, encoding="utf-8")
    (directory / "botocore").mkdir()
    (directory / "botocore/__init__.py").write_text("", encoding="utf-8")
    (directory / "botocore/config.py").write_text(
        "class Config:\n    def __init__(self, **kwargs): self.options = kwargs\n", encoding="utf-8")


def offline_environment(directory: Path, fault=""):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AWS_", "ACADEMY_", "PYTHON"))}
    env.update(PYTHONPATH=str(directory), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               SDK_CAPTURE=str(directory / "sdk-calls.json"), SDK_FAULT=fault,
               PATH=str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""))
    return env


class SecurePutTests(unittest.TestCase):
    def envelope(self, **extra):
        return {"request": {"Name": "/test/api", "Type": "SecureString", "Value": SENTINEL,
                            "Overwrite": False, **extra}, "region": "test-region", "profile": "test-profile"}

    def test_exact_fields_and_optional_omissions_are_preserved(self):
        for extra in ({}, {"Tier": "Advanced", "KeyId": "alias/test", "Description": "test"}):
            envelope = self.envelope(**extra)
            factory = Mock()
            client = factory.return_value.client.return_value
            client.put_parameter.return_value = {"Version": 18, "Tier": "Advanced", "Value": SENTINEL}
            self.assertEqual(bridge.put_secure_parameter(envelope, factory), {"Version": 18, "Tier": "Advanced"})
            factory.assert_called_once_with(region_name="test-region", profile_name="test-profile")
            client.put_parameter.assert_called_once_with(**envelope["request"])

    def test_rejects_invalid_request_before_client_creation(self):
        for changed in ({"Overwrite": "true"}, {"Type": "String"}, {"Value": None}, {"Unexpected": SENTINEL}):
            factory = Mock()
            with self.assertRaisesRegex(bridge.SecurePutError, "^SSM_SECURE_PUT_INVALID_REQUEST$"):
                bridge.put_secure_parameter(self.envelope(**changed), factory)
            factory.assert_not_called()

    def test_rejects_invalid_success_metadata(self):
        for result in ({}, {"Version": True}, {"Version": 0}, {"Version": "1"},
                       {"Version": 1, "Tier": SENTINEL}, None):
            factory = Mock()
            factory.return_value.client.return_value.put_parameter.return_value = result
            with self.assertRaisesRegex(bridge.SecurePutError, "^SSM_SECURE_PUT_INVALID_RESPONSE$"):
                bridge.put_secure_parameter(self.envelope(), factory)

    def test_main_exposes_only_fixed_error_codes(self):
        for error in (RuntimeError(SENTINEL), bridge.SecurePutError(SENTINEL)):
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(sys, "stdin", io.StringIO(json.dumps(self.envelope()))), \
                    patch.object(bridge, "put_secure_parameter", side_effect=error), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(bridge.main(), 2)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "SSM_SECURE_PUT_FAILED\n")

    def test_actual_bridge_stdin_and_sdk_diagnostics(self):
        for fault in ("", "noise", "raise", "no-version"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                install_fake_sdk(directory)
                result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/v1/ssm_secure_put.py")],
                    input=json.dumps(self.envelope()), capture_output=True, text=True, encoding="utf-8",
                    env=offline_environment(directory, fault), timeout=15)
                self.assertNotIn(SENTINEL, result.stdout + result.stderr)
                calls = json.loads((directory / "sdk-calls.json").read_text(encoding="utf-8"))
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["request"], self.envelope()["request"])
                self.assertFalse(calls[0]["secret_in_environment"])
                self.assertNotIn(SENTINEL, " ".join(calls[0]["argv"]))
                self.assertEqual(result.returncode, 2 if fault in ("raise", "no-version") else 0)
                if result.returncode == 0:
                    self.assertEqual(json.loads(result.stdout), {"Version": 412, "Tier": "Advanced"})
                else:
                    self.assertEqual(result.stdout, "")

    def test_sdk_missing_and_invalid_json_fail_without_echo(self):
        for invalid in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                (directory / "boto3.py").write_text("raise ImportError('private SDK import detail')\n")
                result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/v1/ssm_secure_put.py")],
                    input=SENTINEL if invalid else json.dumps(self.envelope()), capture_output=True,
                    text=True, encoding="utf-8", env=offline_environment(directory), timeout=15)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr.strip(), "SSM_SECURE_PUT_INVALID_REQUEST" if invalid
                                 else "SSM_SECURE_PUT_SDK_UNAVAILABLE")

    def test_powershell_real_process_boundary_and_poisoned_output(self):
        for fault in ("", "raise", "poison", "timeout"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                (directory / "core").mkdir()
                shutil.copyfile(ROOT / "scripts/v1/core/aws.ps1", directory / "core/aws.ps1")
                shutil.copyfile(ROOT / "scripts/v1/ssm_secure_put.py", directory / "ssm_secure_put.py")
                install_fake_sdk(directory)
                if fault == "poison":
                    (directory / "ssm_secure_put.py").write_text(
                        "import json,sys\nr=json.load(sys.stdin)['request']\n"
                        "print(r['Value'])\nprint(r['Value'],file=sys.stderr)\n", encoding="utf-8")
                elif fault == "timeout":
                    (directory / "ssm_secure_put.py").write_text("import time\ntime.sleep(10)\n")
                (directory / "request.json").write_text(json.dumps(self.envelope()["request"]), encoding="utf-8")
                (directory / "run.ps1").write_text('''
$ErrorActionPreference = 'Stop'
$script:PlanMode = $false
. (Join-Path $PSScriptRoot 'core/aws.ps1')
$request = Get-Content (Join-Path $PSScriptRoot 'request.json') -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
try {
    Invoke-SsmSecureStringPut -Request $request -Region test-region -TimeoutSeconds 1 | ConvertTo-Json -Compress
} catch { Write-Output $_.Exception.Message; exit 1 }
''', encoding="utf-8")
                result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(directory / "run.ps1")],
                    capture_output=True, text=True, encoding="utf-8", env=offline_environment(directory, fault), timeout=15)
                self.assertNotIn("forbidden-secret-marker", result.stdout + result.stderr)
                self.assertEqual(result.returncode, 0 if not fault else 1)
                if not fault:
                    self.assertEqual(json.loads(result.stdout), {"Version": 412, "Tier": "Advanced"})
                    call = json.loads((directory / "sdk-calls.json").read_text(encoding="utf-8"))[0]
                    self.assertEqual(call["value"], SENTINEL)
                    self.assertNotIn(SENTINEL, " ".join(call["argv"]))
                elif fault == "timeout":
                    self.assertEqual(result.stdout.strip(), "SSM_SECURE_PUT_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
