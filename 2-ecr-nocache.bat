@echo off
title ECR - 노캐시 기반 (수동 실행)
cd /d "%~dp0"
gh workflow run "build-and-push-ecr-nocache.yml" --ref main 2>nul
if errorlevel 1 (
  echo gh CLI가 없거나 로그인 안 됨. GitHub Actions에서 수동 실행하세요.
  echo 브라우저에서 Repo - Actions - "Build and Push to ECR (No Cache)" - Run workflow
  start "" "https://github.com/guswls3028-art/academy-backend/actions/workflows/build-and-push-ecr-nocache.yml"
) else (
  echo 워크플로 실행 요청됨. Actions 탭에서 진행 상황 확인하세요.
)
echo.
pause
