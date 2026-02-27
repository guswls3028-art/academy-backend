#!/bin/bash
# B1 Lambda internal API 검증 - EC2에서 실행 (verify_lambda_internal_api.ps1에서 호출)
# Usage: LIK=key ./_verify_internal_api_remote.sh  OR  LIK_B64=base64key ./_verify_internal_api_remote.sh

if [ -n "$LIK_B64" ]; then
  KEY=$(echo "$LIK_B64" | base64 -d 2>/dev/null || echo "")
else
  KEY="${LIK:-}"
fi
URL="https://api.hakwonplus.com/api/v1/internal/video/backlog-count/"
LOCAL_URL="http://localhost:8000/api/v1/internal/video/backlog-count/"

echo '---LOCAL---'
LOCAL_OUT=$(docker exec -e LIK="$KEY" academy-api python -c '
import os, requests
try:
    r = requests.get("http://localhost:8000/api/v1/internal/video/backlog-count/", headers={"X-Internal-Key": os.environ.get("LIK","")}, timeout=10)
    print(r.text)
    print(r.status_code)
except Exception as e:
    print(str(e))
    print("000")
' 2>/dev/null || echo -e "ERR\n000")
LOCAL_BODY=$(echo "$LOCAL_OUT" | sed '$d')
LOCAL_CODE=$(echo "$LOCAL_OUT" | tail -1)
echo "STATUS:$LOCAL_CODE"
echo "BODY:$LOCAL_BODY"

echo '---PUBLIC---'
PUBLIC_RESP=$(curl -s -w '\n%{http_code}' -H "X-Internal-Key: $KEY" "$URL" 2>/dev/null || echo -e '\n000')
PUBLIC_CODE=$(echo "$PUBLIC_RESP" | tail -1)
PUBLIC_BODY=$(echo "$PUBLIC_RESP" | sed '$d')
echo "STATUS:$PUBLIC_CODE"
echo "BODY:$PUBLIC_BODY"
