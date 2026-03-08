#!/bin/bash
# hot_deploy_watch.sh — ECR image digest watcher for API hot deploy
# Triggered by cron every 2 minutes via hot_deploy_on.sh
#
# Behavior:
#   - Fetches current ECR digest for academy-api:latest
#   - Compares with last deployed digest in STATE_FILE
#   - NEW image  → calls deploy_api_on_server.sh, then updates state
#   - SAME image → logs "no change", exits 0 (no deploy, no restart)
#   - API only: workers are never referenced or restarted
#   - Infinite restart loop prevented: state only written after successful deploy
#
# Usage (direct):  bash scripts/hot_deploy_watch.sh
# Usage (via on):  hot_deploy_on.sh registers this in crontab

set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
ECR_REPO="${ECR_API_REPO:-academy-api}"
ECR_TAG="${ECR_API_TAG:-latest}"
ECR_HOST="${ECR_API_HOST:-809466760795.dkr.ecr.ap-northeast-2.amazonaws.com}"
ECR_FULL_URI="${ECR_API_IMAGE_URI:-${ECR_HOST}/${ECR_REPO}:${ECR_TAG}}"

CONTAINER_NAME="academy-api"
STATE_FILE="${HOT_DEPLOY_STATE_FILE:-/home/ec2-user/.academy-hot-deploy-state}"

# Resolve deploy_api_on_server.sh relative to this script's location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-${SCRIPT_DIR}/deploy_api_on_server.sh}"

TS=$(date -Iseconds)

log() { echo "[$TS] $*"; }

# ── 1) Fetch ECR digest for :latest ──────────────────────────────────────────
ECR_DIGEST=$(aws ecr describe-images \
  --repository-name "$ECR_REPO" \
  --image-ids imageTag="$ECR_TAG" \
  --query 'imageDetails[0].imageDigest' \
  --output text \
  --region "$REGION" 2>/dev/null || true)

if [ -z "$ECR_DIGEST" ] || [ "$ECR_DIGEST" = "None" ]; then
  log "WARN: Could not fetch ECR digest for ${ECR_REPO}:${ECR_TAG}. Skipping. (Check IAM: ecr:DescribeImages)"
  exit 0
fi

# ── 2) Read last deployed digest from state file ──────────────────────────────
LAST_DIGEST=$(grep "^last_ecr_digest=" "$STATE_FILE" 2>/dev/null | head -1 | cut -d= -f2 || true)

# ── 3) Bootstrap: no state file → check running container's RepoDigest ───────
if [ -z "$LAST_DIGEST" ]; then
  REPO_DIGESTS=$(docker image inspect "$ECR_FULL_URI" \
    --format '{{range .RepoDigests}}{{.}} {{end}}' 2>/dev/null || true)
  if echo "$REPO_DIGESTS" | grep -qF "$ECR_DIGEST"; then
    log "BOOTSTRAP: Container already on current ECR image (${ECR_DIGEST:0:19}...). Writing state."
    {
      echo "last_ecr_digest=$ECR_DIGEST"
      echo "last_checked_at=$TS"
      echo "last_deployed_at=bootstrap-$(date -Iseconds)"
      echo "deployed_image=$ECR_FULL_URI"
    } > "$STATE_FILE"
    exit 0
  fi
  log "BOOTSTRAP: No state file. Container digest differs from ECR. Treating as new image."
fi

# ── 4) Compare digests ────────────────────────────────────────────────────────
if [ "$ECR_DIGEST" = "$LAST_DIGEST" ]; then
  log "OK: No new image. digest=${ECR_DIGEST:0:19}..."
  # Update last_checked_at only; leave all other fields intact
  if [ -f "$STATE_FILE" ]; then
    TMP=$(mktemp)
    grep -v "^last_checked_at=" "$STATE_FILE" > "$TMP" || true
    echo "last_checked_at=$TS" >> "$TMP"
    mv "$TMP" "$STATE_FILE"
  fi
  exit 0
fi

# ── 5) New image detected ─────────────────────────────────────────────────────
log "NEW IMAGE DETECTED: ecr=${ECR_DIGEST:0:19}... prev=${LAST_DIGEST:0:19}... Deploying."

if [ ! -x "$DEPLOY_SCRIPT" ]; then
  chmod +x "$DEPLOY_SCRIPT" 2>/dev/null || true
fi

# ── 6) Deploy (API only — deploy_api_on_server.sh never touches workers) ─────
if bash "$DEPLOY_SCRIPT"; then
  # Only write state on success → prevents infinite restart loop on failure
  {
    echo "last_ecr_digest=$ECR_DIGEST"
    echo "last_checked_at=$TS"
    echo "last_deployed_at=$TS"
    echo "deployed_image=$ECR_FULL_URI"
  } > "$STATE_FILE"
  log "OK: Deploy complete. State updated: ${ECR_DIGEST:0:19}..."
else
  log "ERROR: Deploy failed. State NOT updated. Will retry next cycle."
  exit 1
fi
