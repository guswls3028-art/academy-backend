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

# 1. Provision default templates for tenant 2
# 2. Copy solapi_template_id + solapi_status from tenant 1's matching templates
$pyCode = @'
from apps.support.messaging.models import MessageTemplate, AutoSendConfig
from apps.core.models.tenant import Tenant

t2 = Tenant.objects.get(id=2)

# Step 1: Get tenant 1's approved templates as reference
t1_tpls = {t.name: t for t in MessageTemplate.objects.filter(tenant_id=1, solapi_status='APPROVED')}
print(f"Tenant 1 approved templates: {len(t1_tpls)}")

# Step 2: Get or create templates for tenant 2 matching tenant 1
created = 0
updated = 0
for name, t1 in t1_tpls.items():
    t2_tpl, is_new = MessageTemplate.objects.get_or_create(
        tenant=t2, name=name,
        defaults={
            'category': t1.category,
            'subject': t1.subject,
            'body': t1.body,
            'solapi_template_id': t1.solapi_template_id,
            'solapi_status': 'APPROVED',
        }
    )
    if is_new:
        created += 1
        print(f"  CREATED: {name} -> {t1.solapi_template_id}")
    else:
        # Update existing with solapi info
        changed = False
        if t2_tpl.solapi_template_id != t1.solapi_template_id:
            t2_tpl.solapi_template_id = t1.solapi_template_id
            changed = True
        if t2_tpl.solapi_status != 'APPROVED':
            t2_tpl.solapi_status = 'APPROVED'
            changed = True
        if t2_tpl.body != t1.body:
            t2_tpl.body = t1.body
            changed = True
        if t2_tpl.category != t1.category:
            t2_tpl.category = t1.category
            changed = True
        if changed:
            t2_tpl.save()
            updated += 1
            print(f"  UPDATED: {name} -> {t1.solapi_template_id}")
        else:
            print(f"  OK: {name}")

# Step 3: Also set tenant 2 user password for API testing
from django.contrib.auth import get_user_model
U = get_user_model()
# Find tenant 2 owner
from apps.core.models import Membership
m = Membership.objects.filter(tenant_id=2, role='owner').select_related('user').first()
if m:
    m.user.set_password('testpass1234')
    m.user.save()
    print(f"T2 owner password reset: user_id={m.user_id} username={m.user.username}")
else:
    # Find any tenant 2 staff
    m = Membership.objects.filter(tenant_id=2).select_related('user').first()
    if m:
        m.user.set_password('testpass1234')
        m.user.save()
        print(f"T2 user password reset: user_id={m.user_id} username={m.user.username}")

# Verify
approved = MessageTemplate.objects.filter(tenant=t2, solapi_status='APPROVED').count()
print(f"\nResult: created={created}, updated={updated}")
print(f"Tenant 2 approved templates: {approved}")
'@

$bytes = [System.Text.Encoding]::UTF8.GetBytes($pyCode)
$b64 = [Convert]::ToBase64String($bytes)
$shellCmd = "echo $b64 | base64 -d > /tmp/st2.py && docker cp /tmp/st2.py academy-api:/tmp/st2.py && docker exec academy-api python manage.py shell -c 'exec(open(chr(47)+chr(116)+chr(109)+chr(112)+chr(47)+chr(115)+chr(116)+chr(50)+chr(46)+chr(112)+chr(121)).read())'"

$inputObj = @{
    InstanceIds = @($instId)
    DocumentName = "AWS-RunShellScript"
    Parameters = @{ commands = @($shellCmd) }
}
$paramsFile = Join-Path $env:TEMP "ssm-st2.json"
$json = $inputObj | ConvertTo-Json -Depth 3
[System.IO.File]::WriteAllText($paramsFile, $json, [System.Text.UTF8Encoding]::new($false))

$result = & aws ssm send-command --cli-input-json "file://$paramsFile" --region ap-northeast-2 --profile default --output json 2>&1
$parsed = ($result | Out-String).Trim() | ConvertFrom-Json
$cid = $parsed.Command.CommandId
if (-not $cid) { Write-Host "Failed"; exit 1 }
Write-Host "CommandId: $cid"
Start-Sleep 12
for ($w = 0; $w -lt 90; $w += 5) {
    try {
        $inv = & aws ssm get-command-invocation --command-id $cid --instance-id $instId --region ap-northeast-2 --profile default --output json 2>&1
        $invP = ($inv | Out-String).Trim() | ConvertFrom-Json
        if ($invP.Status -eq "Success") { Write-Host $invP.StandardOutputContent; exit 0 }
        if ($invP.Status -in @("Failed","Cancelled","TimedOut")) { Write-Host "OUT: $($invP.StandardOutputContent)"; Write-Host "ERR: $($invP.StandardErrorContent)"; exit 1 }
    } catch {}
    Start-Sleep 5
}
