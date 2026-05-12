#!/bin/bash
# ============================================================================
# DEPRECATED 2026-05-12 — but FUNCTIONAL as cleanup.
# 본 스크립트는 legacy hot-deploy cron 잔재가 EC2 에 등록되어 있을 때 안전하게 제거.
# `hot_deploy_on.sh` 는 deprecated 되었고, off 만 cleanup 용도로 유효.
# 공식 배포 경로: .github/workflows/v1-build-and-push-latest.yml.
# ============================================================================
# hot_deploy_off.sh — Disable ECR-digest-based hot deploy cron (OFF)

set -e

NEW_CRON=$(crontab -l 2>/dev/null | grep -v "hot_deploy_watch.sh" || true)

if [ -n "$NEW_CRON" ]; then
  echo "$NEW_CRON" | crontab -
  echo "OK — Hot Deploy cron removed (OFF)."
else
  crontab -r 2>/dev/null || true
  echo "OK — crontab cleared (Hot Deploy OFF)."
fi
