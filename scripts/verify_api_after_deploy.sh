#!/bin/bash
# EC2 only. Run AFTER deploy_api_on_server.sh.
# Verifies: DATABASES HOST not null, backlog-count returns 200.
# Usage: cd /home/ec2-user/academy && bash scripts/verify_api_after_deploy.sh

set -e
ENV_FILE="${ENV_FILE:-/home/ec2-user/.env}"

echo "[1/3] Check DATABASES in container..."
OUT=$(docker exec academy-api python -c "
from django.conf import settings
import json
print(json.dumps(settings.DATABASES, indent=2, default=str))
" 2>&1) || true

if echo "$OUT" | grep -q '"HOST": null'; then
  echo "FAIL: DATABASES default.HOST is null. SSM or .env missing DB_HOST." >&2
  exit 1
fi
if echo "$OUT" | grep -q '"NAME": null'; then
  echo "FAIL: DATABASES default.NAME is null. SSM or .env missing DB_NAME." >&2
  exit 1
fi
echo "  OK: DATABASES HOST/NAME populated."

echo "[2/3] Check internal backlog-count API (200)..."
KEY=$(grep -E '^LAMBDA_INTERNAL_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r\n' || true)
CODE=$(curl -s -o /tmp/backlog_resp.txt -w "%{http_code}" "http://127.0.0.1:8000/api/v1/internal/video/backlog-count/" -H "X-Internal-Key: ${KEY}" 2>/dev/null || echo "000")
if [ "$CODE" != "200" ]; then
  echo "FAIL: backlog-count returned HTTP $CODE (expected 200)." >&2
  cat /tmp/backlog_resp.txt 2>/dev/null | head -5
  exit 1
fi
echo "  OK: backlog-count = 200."

echo "[3/3] Done. DATABASES OK, backlog-count 200. Lambda metric publish can proceed."
