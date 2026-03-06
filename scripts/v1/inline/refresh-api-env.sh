#!/bin/bash
# API 컨테이너에 최신 SSM env 적용 후 재시작
SSM_PARAM="${1:-/academy/api/env}"
REGION="${2:-ap-northeast-2}"
export AWS_REGION="$REGION"

ENV_JSON=$(aws ssm get-parameter --name "$SSM_PARAM" --with-decryption --query Parameter.Value --output text --region "$REGION" 2>/dev/null) || true
if [ -z "$ENV_JSON" ]; then
  echo "SSM fetch failed"
  exit 1
fi

echo "$ENV_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(k+'='+str(v)) for k,v in d.items()]" 2>/dev/null > /opt/api.env || true
if [ ! -s /opt/api.env ]; then
  echo "api.env write failed"
  exit 1
fi

echo "VIDEO_BATCH from api.env:"
grep VIDEO_BATCH /opt/api.env || true

API_IMG=$(docker inspect academy-api --format '{{.Config.Image}}' 2>/dev/null) || true
if [ -z "$API_IMG" ]; then
  API_IMG=809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
fi

docker stop academy-api 2>/dev/null || true
docker rm academy-api 2>/dev/null || true
docker run -d --restart unless-stopped --name academy-api -p 8000:8000 --env-file /opt/api.env "$API_IMG" 2>&1 || echo "docker run failed"
echo "Done."
