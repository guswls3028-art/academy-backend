# V1 Build 서버 전환 계획

**SSOT:** docs/00-SSOT/v1/params.yaml  
**명칭:** V1 통일

---

## 현재 상태 (당분간 latest 전략)

- **이미지 태그:** academy-* 모두 `latest` 사용 (params.yaml `ecr.useLatestTag: true`).
- **빌드/푸시:** GitHub Actions `.github/workflows/build-and-push-latest.yml` (main push / workflow_dispatch). ARM64, ECR push 태그 `latest`, push 후 digest를 `docs/00-SSOT/v1/reports/ci-build.latest.md`에 기록.
- **배포:** deploy.ps1 매 배포마다 UserData에 배포 nonce(DeploymentId) 포함 → LT 버전 변경 → instance refresh로 최신 `latest` pull·실행. API UserData에서 기존 컨테이너 stop/rm 후 `docker pull` → `docker run --name academy-api`.
- **추적:** 배포 후 API 인스턴스에서 `docker inspect academy-api`로 Image/RepoDigests 수집 → `docs/00-SSOT/v1/reports/runtime-images.latest.md` 기록.

---

## Build 서버(academy-build-arm64) 제거 조건

1. **CI로 latest 빌드/푸시 + 배포 성공을 2회 확인** (수동 또는 자동 배포 후 GATE-A 통과 2회).
2. 이후 build 서버 인스턴스 **Stop** (즉시 비용 절감). 필요 시 동일 AMI/역할로 재기동 가능.
3. SSOT `build.*` 설정은 전환기간 동안만 참조; CI 전환 완료 후에는 사용하지 않음.

---

## 추후 전환: Immutable tag 전략

- 기능 안정화 후 **immutable tag**(예: git SHA, `api-<shortSha>`)로 전환 계획.
- SSOT `ecr.useLatestTag` → `false` 복원, `Get-LatestApiImageUri`가 ECR에서 non-latest 최신 태그 사용.
- CI 워크플로에서 태그를 commit SHA 기반으로 push하고, deploy는 해당 태그만 참조하도록 변경.
- 이 계획은 별도 문서 또는 params.yaml 주석으로 확정 시 반영.
