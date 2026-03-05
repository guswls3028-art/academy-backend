# V1 academy-api 이미지 빌드·ECR 푸시 증거

**SSOT:** docs/00-SSOT/v1/params.yaml  
**리전:** ap-northeast-2  
**ECR 리포지토리:** academy-api (ecr.apiRepo)

## PHASE 1 — 코드 반영 확인 (빌드 전)

- **HealthCheckHostMiddleware:** `apps/api/common/middleware.py` 에 등록됨. `/health`, `/health/` 요청 시 `HTTP_HOST` 를 `127.0.0.1` 로 덮어 ALB Host: private IP → 400 방지.
- **MIDDLEWARE:** `apps/api/config/settings/base.py` 선두에 `HealthCheckHostMiddleware` 배치됨.
- **배포에서 사용하는 이미지:** `Get-LatestApiImageUri` — ECR academy-api 에서 **non-latest 태그 중 최신 푸시** 1개 사용, 없으면 `latest` 사용. immutable 태그 권장(SSOT ecr.immutableTagRequired: true).

## PHASE 2 — 빌드 서버 빌드·푸시

(아래는 빌드·푸시 실행 후 채움)

| 항목 | 값 |
|------|-----|
| 빌드 서버 InstanceId | |
| 빌드 경로 | /opt/academy 또는 $HOME/academy |
| 이미지 태그 | |
| ECR URI | 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:* |
| 푸시 완료 시각 | |
| describe-images digest | |
