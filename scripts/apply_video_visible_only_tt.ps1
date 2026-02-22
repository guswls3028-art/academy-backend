# ==============================================================================
# DEPRECATED: SSOT로 이관됨. scripts/infra/apply_video_asg_scaling_policy.ps1 호출.
# Visible-only (e1=m1, NotVisible 제외)
# ==============================================================================
# 사용: .\scripts\apply_video_visible_only_tt.ps1
#      .\scripts\apply_video_visible_only_tt.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg"  # [DEPRECATED] Video = Batch only
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "Redirecting to SSOT: scripts/infra/apply_video_asg_scaling_policy.ps1" -ForegroundColor Cyan
& (Join-Path $ScriptRoot "infra\apply_video_asg_scaling_policy.ps1") -Region $Region -AsgName $AsgName
