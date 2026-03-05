# 프론트 Git 배포 파이프라인 ↔ V1 SSOT 매핑

**목적:** V1 SSOT(docs/00-SSOT/v1/params.yaml)의 `front.*` / `r2.*` 필드와 프론트 Git 배포 파이프라인(academy-frontend) 동작의 1:1 매핑 정리.  
**프론트 저장소:** guswls3028-art/academy-frontend. 실제 워크플로는 해당 repo의 `.github/workflows/*` 에서 확인.

---

## SSOT 필드 ↔ 파이프라인 매핑

| SSOT 경로 | 설명 | 파이프라인에서의 사용처(예상) |
|-----------|------|------------------------------|
| **front.domains.app** | 앱 공개 도메인 (예: app.hakwonplus.com) | CDN/Pages 라우팅, CORS allowedOrigins에 포함 권장 |
| **front.domains.api** | API 공개 도메인 (예: api.hakwonplus.com) | 프론트→API 요청 베이스 URL, /health 검증용 |
| **front.buildOutputDir** | 빌드 산출물 디렉터리 (기본 dist) | 워크플로 build step 출력 경로 (Vite 등) |
| **front.r2StaticBucket** | 정적 배포용 R2 버킷 (예: academy-storage) | wrangler r2 object put 대상 버킷 |
| **front.r2StaticPrefix** | R2 내 정적 파일 prefix (기본 static/front) | 업로드 경로 prefix (버전 디렉터리 상위) |
| **front.purgeOnDeploy** | 배포 시 CDN 캐시 purge 여부 | true일 때만 Cloudflare purge API 호출 (정책 일관성) |
| **front.cdnCacheControl.assetMaxAge** | 해시 자산 Cache-Control (기본 31536000) | R2/Worker 메타데이터 또는 CDN 캐시 규칙 |
| **front.cdnCacheControl.indexMaxAge** | index.html Cache-Control (기본 0, no-cache) | 동일 |
| **front.cors.allowedOrigins** | CORS 허용 오리진 목록 (와일드카드 금지) | API 서버 CORS 설정 및 검증 시 정적 검사 |
| **r2.bucket** | R2 기본 버킷 (비디오 등) | 공용 R2 설정; 프론트 정적은 front.r2StaticBucket 사용 |
| **r2.publicBaseUrl** | R2 공개 베이스 URL | 정적 자산 공개 URL 구성 시 참고 |

---

## 파이프라인 확인 체크리스트 (academy-frontend repo 기준)

워크플로 파일(`.github/workflows/*.yml`) 확인 시 아래 항목을 채우면 SSOT와 일치 여부를 검증할 수 있다.

| 항목 | 예상 값(SSOT 기준) | 비고 |
|------|--------------------|------|
| 트리거 브랜치 | main (또는 SSOT에 명시된 브랜치) | push/workflow_dispatch |
| build output dir | `front.buildOutputDir` (기본 dist) | Vite: dist |
| 업로드 대상 | R2 버킷 + prefix | `front.r2StaticBucket` + `front.r2StaticPrefix` |
| CDN purge | `front.purgeOnDeploy=true` 일 때만 | Cloudflare API purge_everything 또는 URL 목록 |
| index.html Cache-Control | no-cache 계열 (max-age=0) | `front.cdnCacheControl.indexMaxAge` |
| 해시 자산(JS/CSS) Cache-Control | max-age=31536000 (1년) | `front.cdnCacheControl.assetMaxAge` |

---

## 로컬 배포(deploy.ps1 -DeployFront)와의 관계

- **로컬 배포:** `scripts/v1/deploy.ps1 -DeployFront` → `deploy-front.ps1` 가 SSOT를 읽어 동일한 `front.*` / `r2.*` 값을 사용한다.
- **Git 파이프라인:** CI에서 빌드 후 R2 업로드·purge 시 동일 SSOT 값을 참조하도록 설정하면 단일 진실(SSOT)이 유지된다.
- **검증:** `run-deploy-verification.ps1` 는 SSOT의 `front.domains.app` / `front.domains.api` 가 있으면 프론트 200·캐시 정책·API /health·CORS 정적 검사를 수행하고, 결과를 `deploy-verification-latest.md` 섹션 3에 근거로 기록한다.

---

*생성: V1 배포 검증 자동화. docs/00-SSOT/v1/params.yaml 이 단일 소스.*
