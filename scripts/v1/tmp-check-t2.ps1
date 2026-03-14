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
from apps.core.models.tenant import Tenant
t = Tenant.objects.get(id=2)
print(f"CODE={t.code}")
print(f"NAME={t.name}")
print(f"SENDER={t.messaging_sender}")
print(f"PFID={t.kakao_pfid}")

# Check approved templates for tenant 2
from apps.support.messaging.models import MessageTemplate
tpls = MessageTemplate.objects.filter(tenant=t, solapi_status='APPROVED')
print(f"APPROVED_TEMPLATES={tpls.count()}")
for tp in tpls[:3]:
    print(f"  TPL id={tp.id} solapi_id={tp.solapi_template_id} name={tp.name[:30]}")

# Check students in tenant 2
from apps.domains.students.models import Student
sts = Student.objects.filter(tenant_id=2, deleted_at__isnull=True)[:5]
for s in sts:
    print(f"  STU id={s.id} name={s.name} phone={s.phone} parent={s.parent_phone}")

# Check staff/users for login
from apps.core.models.membership import Membership
members = Membership.objects.filter(tenant_id=2).select_related('user')[:3]
for m in members:
    print(f"  USER id={m.user_id} username={m.user.username} role={m.role}")
'@

$bytes = [System.Text.Encoding]::UTF8.GetBytes($pyCode)
$b64 = [Convert]::ToBase64String($bytes)
$shellCmd = "echo $b64 | base64 -d > /tmp/ct2.py && docker cp /tmp/ct2.py academy-api:/tmp/ct2.py && docker exec academy-api python manage.py shell -c 'exec(open(chr(47)+chr(116)+chr(109)+chr(112)+chr(47)+chr(99)+chr(116)+chr(50)+chr(46)+chr(112)+chr(121)).read())'"

$inputObj = @{
    InstanceIds = @($instId)
    DocumentName = "AWS-RunShellScript"
    Parameters = @{ commands = @($shellCmd) }
}
$paramsFile = Join-Path $env:TEMP "ssm-ct2.json"
$json = $inputObj | ConvertTo-Json -Depth 3
[System.IO.File]::WriteAllText($paramsFile, $json, [System.Text.UTF8Encoding]::new($false))

$result = & aws ssm send-command --cli-input-json "file://$paramsFile" --region ap-northeast-2 --profile default --output json 2>&1
$parsed = ($result | Out-String).Trim() | ConvertFrom-Json
$cid = $parsed.Command.CommandId
if (-not $cid) { Write-Host "Failed"; exit 1 }
Start-Sleep 12
for ($w = 0; $w -lt 60; $w += 5) {
    try {
        $inv = & aws ssm get-command-invocation --command-id $cid --instance-id $instId --region ap-northeast-2 --profile default --output json 2>&1
        $invP = ($inv | Out-String).Trim() | ConvertFrom-Json
        if ($invP.Status -eq "Success") { Write-Host $invP.StandardOutputContent; exit 0 }
        if ($invP.Status -in @("Failed","Cancelled","TimedOut")) { Write-Host "OUT: $($invP.StandardOutputContent)"; Write-Host "ERR: $($invP.StandardErrorContent)"; exit 1 }
    } catch {}
    Start-Sleep 5
}
