#!/bin/bash
# EC2 API 서버에서 실행. SSM /academy/workers/env 를 .env 로 저장.
# AWS CLI --output text 가 줄바꿈을 탭으로 주므로, 탭을 줄바꿈으로 바꿔서 저장 (한 줄로 저장되면 Docker env-file 이 깨짐).
set -e
ENV_FILE="${1:-/home/ec2-user/.env}"
REGION="${2:-ap-northeast-2}"
SSM_NAME="${3:-/academy/workers/env}"
aws ssm get-parameter --name "$SSM_NAME" --with-decryption --query Parameter.Value --output text --region "$REGION" | sed 's/\t/\n/g' | grep -v '^$' > "$ENV_FILE"
echo "OK. SSM -> $ENV_FILE (tab->newline applied). Next: bash scripts/refresh_api_container_env.sh"
