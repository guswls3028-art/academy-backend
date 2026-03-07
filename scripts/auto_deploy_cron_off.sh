#!/bin/bash
# EC2 API 서버에서: 자동 배포 cron 제거 (OFF)
# 사용: bash scripts/auto_deploy_cron_off.sh
# 원격 제어: pwsh scripts/v1/api-auto-deploy-remote.ps1 -Action Off -AwsProfile default

set -e
NEW_CRON=$(crontab -l 2>/dev/null | grep -v "deploy_api_on_server.sh" || true)
if [ -n "$NEW_CRON" ]; then
  echo "$NEW_CRON" | crontab -
  echo "OK — 자동 배포 cron 제거됨 (OFF)."
else
  crontab -r 2>/dev/null || true
  echo "OK — crontab 비움 (자동 배포 OFF)."
fi
