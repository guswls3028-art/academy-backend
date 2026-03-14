$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = "default"
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
. (Join-Path $PSScriptRoot "resources\api.ps1")
$null = Load-SSOT -Env "prod"
$ids = @(Get-APIASGInstanceIds)
$instId = $ids[0]

$pyCode = @'
from django.contrib.auth import get_user_model
U = get_user_model()
# Find users belonging to tenant 2
users = U.objects.filter(tenant_id=2, is_staff=True)[:3]
if not users:
    users = U.objects.filter(tenant_id=2)[:3]
for u in users:
    print(f"user_id={u.id} username={u.username} is_staff={u.is_staff}")
if users:
    u = users[0]
    u.set_password("testpass1234")
    u.save()
    print(f"PASSWORD_SET for user_id={u.id} username={u.username}")

# Verify tenant 2 template count
from apps.support.messaging.models import MessageTemplate
count = MessageTemplate.objects.filter(tenant_id=2, solapi_status="APPROVED").count()
print(f"Tenant2 APPROVED templates: {count}")
'@

$bytes = [System.Text.Encoding]::UTF8.GetBytes($pyCode)
$b64 = [Convert]::ToBase64String($bytes)
$shellCmd = "echo $b64 | base64 -d > /tmp/t2pw.py && docker cp /tmp/t2pw.py academy-api:/tmp/t2pw.py && docker exec academy-api python manage.py shell -c 'exec(open(chr(47)+chr(116)+chr(109)+chr(112)+chr(47)+chr(116)+chr(50)+chr(112)+chr(119)+chr(46)+chr(112)+chr(121)).read())'"

$inputObj = @{
    InstanceIds = @($instId)
    DocumentName = "AWS-RunShellScript"
    Parameters = @{ commands = @($shellCmd) }
}
$paramsFile = Join-Path $env:TEMP "ssm-t2pw.json"
$json = $inputObj | ConvertTo-Json -Depth 3
[System.IO.File]::WriteAllText($paramsFile, $json, [System.Text.UTF8Encoding]::new($false))

$result = & aws ssm send-command --cli-input-json "file://$paramsFile" --region ap-northeast-2 --profile default --output json 2>&1
$parsed = ($result | Out-String).Trim() | ConvertFrom-Json
$cid = $parsed.Command.CommandId
if (-not $cid) { Write-Host "Failed"; exit 1 }
Start-Sleep 10
for ($w = 0; $w -lt 60; $w += 5) {
    try {
        $inv = & aws ssm get-command-invocation --command-id $cid --instance-id $instId --region ap-northeast-2 --profile default --output json 2>&1
        $invP = ($inv | Out-String).Trim() | ConvertFrom-Json
        if ($invP.Status -eq "Success") { Write-Host $invP.StandardOutputContent; exit 0 }
        if ($invP.Status -in @("Failed","Cancelled","TimedOut")) { Write-Host "ERR: $($invP.StandardErrorContent)"; Write-Host $invP.StandardOutputContent; exit 1 }
    } catch {}
    Start-Sleep 5
}
