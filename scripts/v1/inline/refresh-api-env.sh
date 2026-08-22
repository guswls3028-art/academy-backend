#!/bin/bash
# Apply the already-canary-verified API env with local rollback on failure.
set -euo pipefail

SSM_PARAM="${1:-/academy/api/env}"
REGION="${2:-ap-northeast-2}"
ENV_FILE="${ACADEMY_API_ENV_FILE:-/opt/api.env}"
ENV_NEXT="${ENV_FILE}.next"
ENV_PREVIOUS="${ENV_FILE}.previous"
export AWS_REGION="$REGION"

ENV_JSON=$(aws ssm get-parameter \
  --name "$SSM_PARAM" \
  --with-decryption \
  --query Parameter.Value \
  --output text \
  --region "$REGION")
if [ -z "$ENV_JSON" ]; then
  echo "SSM fetch failed" >&2
  exit 1
fi

umask 077
printf '%s' "$ENV_JSON" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
actual = str(data.get("DJANGO_SETTINGS_MODULE", "")).strip()
expected = "apps.api.config.settings.prod"
if actual != expected:
    raise SystemExit(
        f"DJANGO_SETTINGS_MODULE must be {expected!r} (actual={actual!r})"
    )
for key, value in data.items():
    print(f"{key}={value}")
' > "$ENV_NEXT"
test -s "$ENV_NEXT"
test -s "$ENV_FILE"
cp -p "$ENV_FILE" "${ENV_PREVIOUS}.next"
mv "${ENV_PREVIOUS}.next" "$ENV_PREVIOUS"
mv "$ENV_NEXT" "$ENV_FILE"

container=academy-api
rollback_container=academy-api-rollback

restore_previous() {
  set +e
  rollback_failed=false
  if [ -s "$ENV_PREVIOUS" ]; then
    mv "$ENV_PREVIOUS" "$ENV_FILE" || rollback_failed=true
  else
    rollback_failed=true
  fi
  if docker inspect "$rollback_container" >/dev/null 2>&1; then
    docker rm -f "$container" >/dev/null 2>&1 || rollback_failed=true
    docker rename "$rollback_container" "$container" || rollback_failed=true
    docker start "$container" >/dev/null || rollback_failed=true
  elif docker inspect "$container" >/dev/null 2>&1; then
    docker start "$container" >/dev/null || rollback_failed=true
  else
    rollback_failed=true
  fi
  if [ "$rollback_failed" = "true" ]; then
    echo "API_ENV_REFRESH_ROLLBACK_FAILED" >&2
    return 1
  fi
  rollback_required=false
  echo "API_ENV_REFRESH_ROLLBACK_PASS previous container restored" >&2
  return 0
}

on_exit() {
  status=$?
  trap - EXIT
  if [ "$rollback_required" = "true" ] && ! restore_previous; then
    exit 70
  fi
  exit "$status"
}

rollback_required=true
trap on_exit EXIT
api_image=$(docker inspect "$container" --format '{{.Config.Image}}')
test -n "$api_image"

docker rm -f "$rollback_container" >/dev/null 2>&1 || true
docker stop "$container" >/dev/null
docker rename "$container" "$rollback_container"

if ! docker run -d \
  --restart unless-stopped \
  --name "$container" \
  -p 8000:8000 \
  --env-file "$ENV_FILE" \
  "$api_image" >/dev/null; then
  echo "API env refresh docker run failed" >&2
  exit 1
fi

healthy=false
for _ in $(seq 1 24); do
  healthz=$(curl -sS -o /dev/null -w '%{http_code}' \
    --max-time 10 http://127.0.0.1:8000/healthz || true)
  health=$(curl -sS -o /dev/null -w '%{http_code}' \
    --max-time 10 http://127.0.0.1:8000/health || true)
  if [ "$healthz" = "200" ] && [ "$health" = "200" ]; then
    healthy=true
    break
  fi
  sleep 5
done

if [ "$healthy" != "true" ]; then
  docker logs --tail 120 "$container" >&2 || true
  echo "API env refresh health failed" >&2
  exit 1
fi

docker rm -f "$rollback_container" >/dev/null
rm -f "$ENV_PREVIOUS"
rollback_required=false
trap - EXIT
echo "API_ENV_REFRESH_PASS healthz=200 health=200"
