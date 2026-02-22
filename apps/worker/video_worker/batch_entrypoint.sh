#!/bin/bash
# Fetch /academy/workers/env from SSM and export, then exec main command.
# Job role must have ssm:GetParameter for academy/*

set -e
REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-ap-northeast-2}}"
SSM_NAME="${BATCH_SSM_ENV:-/academy/workers/env}"

content=$(aws ssm get-parameter --name "$SSM_NAME" --with-decryption --query Parameter.Value --output text --region "$REGION" 2>/dev/null || true)
if [ -n "$content" ]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)='(.*)'$ ]] || [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      export "$line"
    fi
  done <<< "$content"
fi
exec "$@"
