#!/bin/bash
# EC2 API 서버에서: 자동 배포 cron 제거 (OFF) — DISABLED IN PRODUCTION.
# Rapid deploy is disabled in production. Use CI/CD formal deploy only.
set -e
echo "Rapid deploy is disabled in production. Use CI/CD formal deploy." >&2
exit 1

NEW_CRON=$(crontab -l 2>/dev/null | grep -v "deploy_api_on_server.sh" || true)
if [ -n "$NEW_CRON" ]; then
  echo "$NEW_CRON" | crontab -
  echo "OK — 자동 배포 cron 제거됨 (OFF)."
else
  crontab -r 2>/dev/null || true
  echo "OK — crontab 비움 (자동 배포 OFF)."
fi
