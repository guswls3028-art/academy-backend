#!/bin/bash
# ============================================================================
# DEPRECATED 2026-05-12 — DO NOT USE. DEAD CODE.
# hot_deploy_watch.sh 가 호출하는 deploy_api_on_server.sh 가 production hard-disabled.
# 본 스크립트는 cron 등록만 할 뿐 결과적으로 아무 배포도 일어나지 않음.
# 공식 배포 경로: .github/workflows/v1-build-and-push-latest.yml (CI/CD 자동 처리).
# ============================================================================
# hot_deploy_on.sh — Enable ECR-digest-based hot deploy cron (ON) — LEGACY
echo "DEPRECATED: scripts/hot_deploy_on.sh is dead code. Use CI/CD instead. No-op." >&2
exit 0

set -e

REPO_DIR="${REPO_DIR:-/home/ec2-user/academy}"
LOG_FILE="${LOG_FILE:-/home/ec2-user/hot_deploy.log}"
LOCK_FILE="${LOCK_FILE:-/tmp/academy_hot_deploy.lock}"
WATCH_SCRIPT="$REPO_DIR/scripts/hot_deploy_watch.sh"

# Ensure watch script is executable
chmod +x "$WATCH_SCRIPT" 2>/dev/null || true

# Cron: every 2 minutes, non-blocking flock prevents duplicate runs
CRON_LINE="*/2 * * * * flock -n $LOCK_FILE bash -c 'cd $REPO_DIR && bash scripts/hot_deploy_watch.sh' >> $LOG_FILE 2>&1"

if crontab -l 2>/dev/null | grep -q "hot_deploy_watch.sh"; then
  echo "OK — Hot Deploy already ON. (crontab -l to verify)"
  echo "Log: tail -f $LOG_FILE"
  echo "State: cat /home/ec2-user/.academy-hot-deploy-state"
else
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
  echo "OK — Hot Deploy ON. ECR digest check every 2 minutes (API only)."
  echo "Log:   tail -f $LOG_FILE"
  echo "State: cat /home/ec2-user/.academy-hot-deploy-state"
  echo "Off:   bash $REPO_DIR/scripts/hot_deploy_off.sh"
fi
