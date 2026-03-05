# V1 최종 배포 검증 보고서

**명칭:** V1 통일. **SSOT:** docs/00-SSOT/v1/params.yaml. **배포:** scripts/v1/deploy.ps1. **리전:** ap-northeast-2.

## 요약
| 항목 | 값 |
|------|-----|
| 검증 시각 | 2026-03-06T07:13:57.8746828+09:00 |
| 최종 상태 | FAIL |
| GO/NO-GO | **NO-GO** |

FAIL 항목 해결 후 재검증 필요. ECR 빌드/푸시는 OIDC 전용 `v1-build-and-push-latest.yml`로 통일됨. CI로 latest 푸시 후 deploy·검증 재실행 시 GATE 통과 예상.

## 상세 보고서
- [deploy-verification-latest.md](./deploy-verification-latest.md) — 인프라·Smoke·프론트·SQS·Video·관측·GO/NO-GO 상세
- [ci-build.latest.md](./ci-build.latest.md) — CI 푸시 digest (OIDC 빌드)
- [runtime-images.latest.md](./runtime-images.latest.md) — API 인스턴스 실제 실행 이미지 digest
- [audit.latest.md](./audit.latest.md) — 리소스·지표 스냅샷
- [drift.latest.md](./drift.latest.md) — SSOT 대비 drift


