#!/bin/bash
# EC2 API 서버에서: git push 감지 시 자동 배포 cron 등록 (ON)
# 사용: bash scripts/auto_deploy_cron_on.sh
# 실행 후 1분마다 main 변경 감지 → deploy_api_on_server.sh 실행

set -e
REPO_DIR="${REPO_DIR:-/home/ec2-user/academy}"
LOG_FILE="${LOG_FILE:-/home/ec2-user/auto_deploy.log}"
LOCK_FILE="${LOCK_FILE:-/tmp/academy_deploy.lock}"

CRON_LINE="* * * * * flock -n $LOCK_FILE bash -c 'cd $REPO_DIR && git fetch origin main && LOCAL=\$(git rev-parse HEAD) && REMOTE=\$(git rev-parse origin/main) && if [ \"\$LOCAL\" != \"\$REMOTE\" ]; then echo \"[\$(date -Iseconds)] Deploying...\" && bash scripts/deploy_api_on_server.sh; fi' >> $LOG_FILE 2>&1"

if ! crontab -l 2>/dev/null | grep -q "deploy_api_on_server.sh"; then
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
  echo "OK — 자동 배포 ON. 1분마다 main 변경 시 배포합니다."
  echo "로그: tail -f $LOG_FILE"
  echo "상태: crontab -l"
else
  echo "이미 자동 배포 cron이 등록되어 있습니다. (crontab -l 로 확인)"
fi
