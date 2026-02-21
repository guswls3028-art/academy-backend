#!/bin/bash
# API 서버(EC2)에서 실행.
# SSM /academy/workers/env 내용을 가져와서 기존 .env와 병합한다.
# - SSM에 있는 키: SSM 값으로 덮어씀.
# - 기존 .env에만 있는 키(R2, VIDEO_BUCKET 등): 유지.
# 이렇게 하면 "SSM sync" 시 로컬에만 있던 변수가 날아드는 일을 막을 수 있다.
set -e
ENV_FILE="${1:-/home/ec2-user/.env}"
REGION="${2:-ap-northeast-2}"
SSM_NAME="/academy/workers/env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found: $ENV_FILE"
  exit 1
fi

TMP_SSM=$(mktemp)
TMP_MERGED=$(mktemp)
trap "rm -f $TMP_SSM $TMP_MERGED" EXIT

echo "Fetching SSM $SSM_NAME..."
aws ssm get-parameter --name "$SSM_NAME" --with-decryption --query Parameter.Value --output text --region "$REGION" 2>/dev/null | tr '\t' '\n' > "$TMP_SSM" || { echo "FAIL: SSM get-parameter"; exit 1; }

# SSM에 있는 키 집합 (키= 제거한 키만)
declare -A SSM_KEYS
while IFS= read -r line; do
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
    SSM_KEYS["${BASH_REMATCH[1]}"]=1
  fi
done < "$TMP_SSM"

# 1) SSM 내용을 먼저 씀 (SSM 우선)
cat "$TMP_SSM" > "$TMP_MERGED"

# 2) 기존 .env에서 SSM에 없는 키만 추가 (로컬 전용 변수 유지)
while IFS= read -r line; do
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
    key="${BASH_REMATCH[1]}"
    if [[ -z "${SSM_KEYS[$key]:-}" ]]; then
      echo "$line" >> "$TMP_MERGED"
    fi
  fi
done < "$ENV_FILE"

cp "$TMP_MERGED" "$ENV_FILE"
echo "OK. Merged SSM into $ENV_FILE (existing-only keys preserved)."
echo "Run: bash scripts/refresh_api_container_env.sh  # to apply to container"
