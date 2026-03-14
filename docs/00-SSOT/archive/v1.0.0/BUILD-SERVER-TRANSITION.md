# V1 Build 서버 제거 완료 (GitHub Actions only)

**SSOT:** docs/00-SSOT/v1/params.yaml  
**명칭:** V1 통일

---

## 현재 상태 (latest 전략 + GitHub Actions only)

- **이미지 태그:** academy-* 모두 `latest` 사용 (params.yaml `ecr.useLatestTag: true`).
- **빌드/푸시:** GitHub Actions **OIDC 전용** `.github/workflows/v1-build-and-push-latest.yml` (main push / workflow_dispatch). Access Key 워크플로우 폐기. `secrets.AWS_ROLE_ARN_FOR_ECR_BUILD` 사용. ARM64, 5개 이미지 latest 푸시 후 digest를 `docs/00-SSOT/v1/reports/ci-build.latest.md`에 기록.
- **자동 배포 (CI):** 빌드·푸시 완료 후 `deploy-api-refresh` job이 API ASG instance refresh를 실행. IAM 역할 `academy-gha-ecr-build`에 `autoscaling:StartInstanceRefresh` 등 권한 적용 완료 (2026-03-11). **push=서버 반영** 자동화 달성.
- **수동 배포:** deploy.ps1 매 배포마다 UserData에 배포 nonce(DeploymentId) 포함 → LT 버전 변경 → instance refresh로 최신 `latest` pull·실행. API UserData에서 기존 컨테이너 stop/rm 후 `docker pull` → `docker run --name academy-api`.
- **추적:** 배포 후 API 인스턴스에서 `docker inspect academy-api`로 Image/RepoDigests 수집 → `docs/00-SSOT/v1/reports/runtime-images.latest.md` 기록. `ci-build.latest.md`의 academy-api digest와 런타임 RepoDigests 불일치 시 보고서에 "CI vs Runtime: MISMATCH" 명시.

---

## Build 서버(academy-build-arm64)

- **사용하지 않음(0대).**
- SSOT에 `build.*`는 더 이상 존재하지 않으며, 빌드 서버를 트리거하는 스크립트도 제거되었다.

---

## 추후 전환: Immutable tag 전략

- 기능 안정화 후 **immutable tag**(예: git SHA, `api-<shortSha>`)로 전환 계획.
- SSOT `ecr.useLatestTag` → `false` 복원, `Get-LatestApiImageUri`가 ECR에서 non-latest 최신 태그 사용.
- CI 워크플로에서 태그를 commit SHA 기반으로 push하고, deploy는 해당 태그만 참조하도록 변경.
- 이 계획은 별도 문서 또는 params.yaml 주석으로 확정 시 반영.
