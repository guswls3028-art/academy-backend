# ==============================================================================
# DEPRECATED: SSOT로 이관됨. scripts/infra/apply_video_asg_scaling_policy.ps1 호출로 대체.
# Video ASG TargetTracking 정책 = Visible only (video_visible_only_tt.json)
# ==============================================================================
# 사용: .\scripts\update_video_tt_target.ps1
#      .\scripts\update_video_tt_target.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg"  # [DEPRECATED] Video = Batch only
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "Redirecting to SSOT: scripts/infra/apply_video_asg_scaling_policy.ps1" -ForegroundColor Cyan
& (Join-Path $ScriptRoot "infra\apply_video_asg_scaling_policy.ps1") -Region $Region -AsgName $AsgName
