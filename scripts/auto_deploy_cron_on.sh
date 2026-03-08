#!/bin/bash
# EC2 API 서버에서: git 레포 기준 자동 배포 cron 등록 (ON) — DISABLED IN PRODUCTION.
# Rapid deploy is disabled in production. Use CI/CD formal deploy only.
set -e
echo "Rapid deploy is disabled in production. Use CI/CD formal deploy." >&2
exit 1

REPO_DIR="${REPO_DIR:-/home/ec2-user/academy}"
LOG_FILE="${LOG_FILE:-/home/ec2-user/auto_deploy.log}"
LOCK_FILE="${LOCK_FILE:-/tmp/academy_deploy.lock}"

# 2분마다 실행: origin/main 변경 시에만 deploy_api_on_server.sh 실행 (정석 배포와 동일: ECR + /opt/api.env)
CRON_LINE="*/2 * * * * flock -n $LOCK_FILE bash -c 'cd $REPO_DIR && git fetch origin main && LOCAL=\$(git rev-parse HEAD) && REMOTE=\$(git rev-parse origin/main) && if [ \"\$LOCAL\" != \"\$REMOTE\" ]; then echo \"[\$(date -Iseconds)] Deploying...\" && git reset --hard origin/main && bash scripts/deploy_api_on_server.sh; fi' >> $LOG_FILE 2>&1"

if ! crontab -l 2>/dev/null | grep -q "deploy_api_on_server.sh"; then
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
  echo "OK — 자동 배포 ON. 2분마다 main 변경 시 배포합니다."
  echo "로그: tail -f $LOG_FILE"
  echo "상태: crontab -l"
else
  echo "이미 자동 배포 cron이 등록되어 있습니다. (crontab -l 로 확인)"
fi
