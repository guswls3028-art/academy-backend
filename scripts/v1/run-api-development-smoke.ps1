# Synthetic real-use smoke tests for the active, isolated development runtime.
# Creates no database rows and removes its development-only R2 objects.
[CmdletBinding()]
param(
    [ValidatePattern('^$|^i-[0-9a-f]+$')]
    [string]$InstanceId = "",
    [ValidateRange(60, 600)]
    [int]$TimeoutSec = 180,
    [switch]$Ci = $false,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ($Ci) {
    Remove-Item Env:AWS_PROFILE -ErrorAction SilentlyContinue
} elseif ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }

$script:PlanMode = $false
. (Join-Path $ScriptRoot "core\env.ps1")
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
Assert-AwsMutationIdentity | Out-Null
Load-SSOT -Env prod | Out-Null

if (-not $script:ApiDevelopmentEnabled) {
    throw "Persistent API development environment is disabled in params.yaml."
}
if ($script:ApiDevelopmentAccessMode -ne "ssm-only") {
    throw "API development access must remain ssm-only."
}

$describeArgs = @("ec2", "describe-instances")
if ($InstanceId) {
    $describeArgs += @("--instance-ids", $InstanceId)
} else {
    $describeArgs += @(
        "--filters",
        "Name=tag:Name,Values=$($script:ApiDevelopmentInstanceName)",
        "Name=tag:ManagedBy,Values=$($script:ApiDevelopmentManagedByTag)",
        "Name=tag:Lifecycle,Values=active",
        "Name=instance-state-name,Values=running"
    )
}
$describeArgs += @("--region", $script:Region, "--output", "json")
$result = Invoke-AwsJson $describeArgs
$instances = @($result.Reservations.Instances | Where-Object { $_.InstanceId })
if ($instances.Count -ne 1 -or [string]$instances[0].State.Name -ne "running") {
    throw "Expected exactly one running API development instance; actual=$($instances.Count)."
}
$instance = $instances[0]
$instanceId = [string]$instance.InstanceId
$tags = @{}
foreach ($tag in @($instance.Tags)) {
    $tags[[string]$tag.Key] = [string]$tag.Value
}
if (
    $tags["Name"] -ne $script:ApiDevelopmentInstanceName -or
    $tags["ManagedBy"] -ne $script:ApiDevelopmentManagedByTag -or
    $tags["Lifecycle"] -notin @("candidate", "active")
) {
    throw "Target instance is outside the managed API development boundary."
}
$toolsSmoke = @'
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import fitz
from hwpx import HwpxDocument
from openpyxl import Workbook
from PIL import Image
from pptx import Presentation

from academy.application.services.excel_parsing_service import parse_student_excel_file
from academy.domain.tools.ppt_composer import PptComposer, PptConfig

started = time.perf_counter()
with tempfile.TemporaryDirectory(prefix="academy-development-smoke-") as temp_dir:
    excel_path = Path(temp_dir) / "students.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["이름", "학부모전화", "학교", "학년"])
    sheet.append(["개발검증학생", "010-1234-5678", "검증고", "2"])
    # Preserve a readable source layout; the renderer must not reshape user sheets.
    for column, width in (("A", 20), ("B", 20), ("C", 20), ("D", 8)):
        sheet.column_dimensions[column].width = width
    workbook.save(excel_path)

    excel_started = time.perf_counter()
    rows, _lecture_title = parse_student_excel_file(str(excel_path))
    excel_seconds = time.perf_counter() - excel_started
    assert len(rows) == 1
    assert rows[0]["name"] == "개발검증학생"
    assert rows[0]["parent_phone"] == "01012345678"
    assert rows[0]["school"] == "검증고"

    # The exact deployed worker must preserve numeric contacts and report a
    # malformed student number instead of creating a different login identity.
    import_path = Path(temp_dir) / "student-import-validation.xlsx"
    import_workbook = Workbook()
    import_sheet = import_workbook.active
    import_sheet.append(["이름", "학부모전화번호", "학생전화번호", "학교유형", "학년"])
    import_sheet.append(["숫자연락처학생", 1077778888, 1099990000, "MIDDLE", 2])
    import_sheet.append(["번호오타학생", 1077778888, "0101234567", "MIDDLE", 2])
    import_sheet.append(["번호없는학생", 1077778888, "", "ELEMENTARY", 6])
    import_workbook.save(import_path)
    import_errors = []
    import_rows, _ = parse_student_excel_file(str(import_path), validation_errors_out=import_errors)
    assert len(import_rows) == 2
    assert import_rows[0]["phone"] == "01099990000"
    assert import_rows[0]["parent_phone"] == "01077778888"
    assert import_rows[0]["school_type"] == "MIDDLE"
    assert import_rows[1]["phone"] is None
    assert import_rows[1]["school_type"] == "ELEMENTARY"
    assert [error["row"] for error in import_errors] == [3]

    image_buffer = io.BytesIO()
    Image.new("RGB", (160, 90), "white").save(image_buffer, format="PNG")
    ppt_started = time.perf_counter()
    composer = PptComposer(PptConfig(aspect_ratio="16:9", background="white"))
    composer.add_slide(image_buffer.getvalue())
    pptx_bytes = composer.finalize()
    ppt_seconds = time.perf_counter() - ppt_started
    presentation = Presentation(io.BytesIO(pptx_bytes))
    assert len(presentation.slides) == 1
    assert pptx_bytes.startswith(b"PK")
    assert len(pptx_bytes) > 1_000

    # Exercise both pinned native renderers in the exact candidate image before
    # development promotion, with the same credential-free child environment.
    hangul_path = Path(temp_dir) / "report.hwpx"
    with HwpxDocument.new() as report:
        report.paragraphs[0].text = "공개 분석 보고서"
        report.add_paragraph("합성 검증 자료이며 실제 사용자 정보를 포함하지 않습니다.")
        hangul_path.write_bytes(report.to_bytes())
    reader_environment = {
        "PATH": os.defpath, "LANG": "C.UTF-8", "HOME": temp_dir,
        "PYTHONPATH": os.pathsep.join(dict.fromkeys(
            [str(Path.cwd()), *[entry for entry in sys.path if entry and Path(entry).is_absolute()]]
        )),
    }
    reader_started = time.perf_counter()
    for source, extension, expected in (
        (hangul_path, "hwpx", "공개 분석 보고서"),
        (excel_path, "xlsx", "개발검증학생"),
    ):
        output = Path(temp_dir) / f"reader-{extension}"
        subprocess.run([
            sys.executable, "-m", "apps.infrastructure.storage.resource_document_renderer",
            str(source), str(output), extension,
        ], env=reader_environment, cwd=temp_dir, check=True, timeout=150,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["pages"] == 1
        assert manifest["pdf"] == "pages.pdf"
        with fitz.open(output / "pages.pdf") as rendered:
            # PDF text extraction may omit visually preserved inter-word spaces.
            extracted = "".join(page.get_text() for page in rendered)
            assert "".join(expected.split()) in "".join(extracted.split()), (
                f"{extension} rendered PDF is missing expected glyphs"
            )
        assert manifest["mode"] == "pages"
    reader_seconds = time.perf_counter() - reader_started

total_seconds = time.perf_counter() - started
assert excel_seconds < 30
assert ppt_seconds < 30
assert total_seconds < 60
print(json.dumps({
    "status": "TOOLS_SMOKE_PASS",
    "excel_seconds": round(excel_seconds, 3),
    "ppt_seconds": round(ppt_seconds, 3),
    "reader_seconds": round(reader_seconds, 3),
    "total_seconds": round(total_seconds, 3),
    "pptx_bytes": len(pptx_bytes),
}, sort_keys=True))
'@

$r2Smoke = @'
import json
import time
import uuid

import boto3
from django.conf import settings

assert settings.R2_STORAGE_BUCKET.startswith("academy-development-")
client = boto3.client(
    "s3",
    endpoint_url=settings.R2_ENDPOINT,
    aws_access_key_id=settings.R2_ACCESS_KEY,
    aws_secret_access_key=settings.R2_SECRET_KEY,
    region_name=settings.R2_REGION,
)
key = f"runtime-smoke/{uuid.uuid4().hex}.txt"
body = b"academy-development-runtime-smoke"
started = time.perf_counter()
try:
    client.put_object(Bucket=settings.R2_STORAGE_BUCKET, Key=key, Body=body)
    response = client.get_object(Bucket=settings.R2_STORAGE_BUCKET, Key=key)
    assert response["Body"].read() == body
finally:
    client.delete_object(Bucket=settings.R2_STORAGE_BUCKET, Key=key)
elapsed = time.perf_counter() - started
assert elapsed < 30
print(json.dumps({
    "status": "R2_SMOKE_PASS",
    "round_trip_seconds": round(elapsed, 3),
}, sort_keys=True))
'@

$toolsB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($toolsSmoke))
$r2B64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($r2Smoke))
$remote = @"
set -euo pipefail
tools_output=`$(printf '%s' '$toolsB64' | base64 -d | docker exec -i academy-tools-development python -)
printf '%s\n' "`$tools_output"
printf '%s' "`$tools_output" | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "TOOLS_SMOKE_PASS"'
api_r2_output=`$(printf '%s' '$r2B64' | base64 -d | docker exec -i academy-api python -)
printf '%s\n' "`$api_r2_output"
printf '%s' "`$api_r2_output" | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "R2_SMOKE_PASS"'
worker_r2_output=`$(printf '%s' '$r2B64' | base64 -d | docker exec -i academy-ai-development python -)
printf '%s\n' "`$worker_r2_output"
printf '%s' "`$worker_r2_output" | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "R2_SMOKE_PASS"'
echo DEVELOPMENT_REAL_USE_SMOKE_PASS
"@
$remote = $remote.Replace("`r", "")
$paramsRef = Convert-JsonArgToFileRef (
    @{
        commands = @($remote)
        executionTimeout = @([string]$TimeoutSec)
    } | ConvertTo-Json -Compress
)
$paramsFile = $paramsRef -replace '^file://', ''
try {
    $sent = Invoke-AwsJson @(
        "ssm", "send-command",
        "--instance-ids", $instanceId,
        "--document-name", "AWS-RunShellScript",
        "--parameters", $paramsRef,
        "--timeout-seconds", [string]$TimeoutSec,
        "--region", $script:Region,
        "--comment", "Synthetic Excel, PPT, and API/AI-worker development-only R2 smoke",
        "--output", "json"
    )
} finally {
    Remove-TempFiles @($paramsFile)
}
$commandId = [string]$sent.Command.CommandId
if (-not $commandId) {
    throw "Development real-use smoke returned no SSM command id."
}

$invocation = $null
for ($elapsed = 0; $elapsed -lt $TimeoutSec; $elapsed += 5) {
    Start-Sleep -Seconds 5
    $invocation = Invoke-AwsJson @(
        "ssm", "get-command-invocation",
        "--command-id", $commandId,
        "--instance-id", $instanceId,
        "--region", $script:Region,
        "--output", "json"
    )
    if ($invocation.Status -in @("Success", "Failed", "Cancelled", "TimedOut")) {
        break
    }
}
if (-not $invocation -or [string]$invocation.Status -ne "Success") {
    $status = if ($invocation) { [string]$invocation.Status } else { "TimedOut" }
    $stderr = if ($invocation) { [string]$invocation.StandardErrorContent } else { "" }
    throw "Development real-use smoke failed: status=$status stderr=$stderr"
}
$output = [string]$invocation.StandardOutputContent
if ($output -notmatch "DEVELOPMENT_REAL_USE_SMOKE_PASS") {
    throw "Development real-use smoke success marker is missing."
}
Write-Host $output.Trim()
