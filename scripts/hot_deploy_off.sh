#!/bin/bash
# hot_deploy_off.sh — Disable ECR-digest-based hot deploy cron (OFF)
#
# Removes the hot_deploy_watch.sh cron entry.
# Idempotent: safe to run even if cron is not registered.
#
# Usage:
#   bash scripts/hot_deploy_off.sh
#
# Remote control: pwsh scripts/v1/hot-deploy-remote.ps1 -Action Off

set -e

NEW_CRON=$(crontab -l 2>/dev/null | grep -v "hot_deploy_watch.sh" || true)

if [ -n "$NEW_CRON" ]; then
  echo "$NEW_CRON" | crontab -
  echo "OK — Hot Deploy cron removed (OFF)."
else
  crontab -r 2>/dev/null || true
  echo "OK — crontab cleared (Hot Deploy OFF)."
fi
