#!/bin/bash
# hot_deploy_on.sh — Enable ECR-digest-based hot deploy cron (ON)
#
# Registers a cron job that runs hot_deploy_watch.sh every 2 minutes.
# Only deploys if ECR has a new academy-api image. Workers are never touched.
# Idempotent: safe to run multiple times.
#
# Usage:
#   bash scripts/hot_deploy_on.sh
#   REPO_DIR=/home/ec2-user/academy bash scripts/hot_deploy_on.sh
#
# Remote control: pwsh scripts/v1/hot-deploy-remote.ps1 -Action On

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
