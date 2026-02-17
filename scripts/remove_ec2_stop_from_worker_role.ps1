# ==============================================================================
# academy-ec2-role에서 ec2:StopInstances 권한 제거 (Worker self-stop 루프 차단)
# 사용: .\scripts\remove_ec2_stop_from_worker_role.ps1
# 전제: IAM put-role-policy 권한 필요 (root 또는 IAM admin)
# ==============================================================================

$ErrorActionPreference = "Stop"
$Region = "ap-northeast-2"
$InstanceProfileName = "academy-ec2-role"

Write-Host "`n=== STEP 1: Role 확인 ===" -ForegroundColor Cyan
$roleName = aws iam get-instance-profile --instance-profile-name $InstanceProfileName --query "InstanceProfile.Roles[0].RoleName" --output text 2>$null
if (-not $roleName) {
    Write-Host "Instance profile '$InstanceProfileName' not found." -ForegroundColor Red
    exit 1
}
Write-Host "Role: $roleName" -ForegroundColor Green

Write-Host "`n=== STEP 2: Attached Policies 조회 ===" -ForegroundColor Cyan
$managed = aws iam list-attached-role-policies --role-name $roleName --query "AttachedPolicies[*].PolicyArn" --output text
$inline = aws iam list-role-policies --role-name $roleName --query "PolicyNames" --output text

Write-Host "Managed policies: $managed"
Write-Host "Inline policies: $inline"

# ec2:StopInstances를 포함하는 정책 찾기
$policyWithStop = $null
$policyType = $null
$policyDoc = $null

foreach ($p in ($inline -split "\s+")) {
    if (-not $p) { continue }
    $doc = aws iam get-role-policy --role-name $roleName --policy-name $p --query "PolicyDocument" --output json 2>$null
    if ($doc -match "StopInstances") {
        $policyWithStop = $p
        $policyType = "inline"
        $policyDoc = $doc | ConvertFrom-Json
        break
    }
}

if (-not $policyWithStop -and $managed) {
    foreach ($arn in ($managed -split "\s+")) {
        if (-not $arn) { continue }
        $defaultVersion = aws iam get-policy --policy-arn $arn --query "Policy.DefaultVersionId" --output text 2>$null
        $doc = aws iam get-policy-version --policy-arn $arn --version-id $defaultVersion --query "PolicyVersion.Document" --output json 2>$null
        if ($doc -match "StopInstances") {
            $policyWithStop = $arn
            $policyType = "managed"
            $policyDoc = ($doc | ConvertFrom-Json)
            break
        }
    }
}

if (-not $policyWithStop) {
    Write-Host "`n ec2:StopInstances를 포함하는 정책이 없습니다. 이미 제거되었거나 다른 경로로 부여됨." -ForegroundColor Yellow
    Write-Host "aws iam simulate-principal-policy 로 확인:" -ForegroundColor Gray
    Write-Host "  aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/$roleName --action-names ec2:StopInstances --region $Region" -ForegroundColor Gray
    exit 0
}

Write-Host "`n발견: $policyType policy '$policyWithStop' 에 ec2:StopInstances 포함" -ForegroundColor Yellow

if ($policyType -eq "managed") {
    Write-Host "`nManaged policy는 직접 수정 불가. 새 Inline policy로 필요한 권한만 부여하거나,"
    Write-Host "해당 Managed policy를 Detach 후 StopInstances 제외한 새 정책을 Attach 해야 함."
    Write-Host "수동 처리 필요. Policy ARN: $policyWithStop" -ForegroundColor Red
    exit 1
}

# Inline policy 수정: ec2:StopInstances 제거
$modified = $false
foreach ($stmt in $policyDoc.Statement) {
    $actions = $stmt.Action
    if ($actions -is [string]) { $actions = @($actions) }
    $newActions = $actions | Where-Object { $_ -ne "ec2:StopInstances" }
    if ($newActions.Count -lt $actions.Count) {
        if ($newActions.Count -eq 0) {
            $stmt.PSObject.Properties.Remove("Action")
        } else {
            $stmt.Action = @($newActions)
        }
        $modified = $true
    }
    # ec2:* 등 와일드카드 확인
    $wildcard = $actions | Where-Object { $_ -match "^ec2:\*$" }
    if ($wildcard) {
        Write-Host "ec2:* 와일드카드 발견. StopInstances만 제거하려면 세분화된 ec2 권한으로 교체 필요." -ForegroundColor Yellow
    }
}

if (-not $modified) {
    Write-Host "정책 구조 확인 필요. 수동 검토 권장." -ForegroundColor Yellow
    exit 1
}

$newDocPath = Join-Path $env:TEMP "academy_workers_modified_$(Get-Date -Format 'yyyyMMddHHmmss').json"
$policyDoc | ConvertTo-Json -Depth 10 -Compress | Set-Content $newDocPath -Encoding UTF8
$newDocUri = "file:///$($newDocPath -replace '\\','/' -replace ' ', '%20')"

Write-Host "`n=== STEP 3: 수정된 정책 적용 ===" -ForegroundColor Cyan
Write-Host "Policy: $policyWithStop"
aws iam put-role-policy --role-name $roleName --policy-name $policyWithStop --policy-document "file://$newDocPath"
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED. IAM put-role-policy 권한 확인." -ForegroundColor Red
    Remove-Item $newDocPath -Force -ErrorAction SilentlyContinue
    exit 1
}
Remove-Item $newDocPath -Force -ErrorAction SilentlyContinue
Write-Host "OK. ec2:StopInstances 제거 완료." -ForegroundColor Green

Write-Host "`n=== 검증 ===" -ForegroundColor Cyan
$after = aws iam get-role-policy --role-name $roleName --policy-name $policyWithStop --query "PolicyDocument" --output json
if ($after -match "StopInstances") {
    Write-Host "경고: 정책에 여전히 StopInstances 포함될 수 있음. 재확인 필요." -ForegroundColor Yellow
} else {
    Write-Host "ec2:StopInstances 미포함 확인됨." -ForegroundColor Green
}
Write-Host "`nDone. Worker self-stop 호출은 이제 실패하며 인스턴스는 유지됩니다." -ForegroundColor Green
