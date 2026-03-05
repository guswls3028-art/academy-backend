# V1 배포 아키텍처 및 표준화 보고서 (Macro Deployment Report)

**최종 갱신일:** 2026-03-06  
**기준:** V1 SSOT (`docs/00-SSOT/v1/`)  
**배포 진입점:** `scripts/v1/deploy.ps1` · **상세 플랜:** `docs/00-SSOT/v1/V1-DEPLOYMENT-PLAN.md`

---

## 0. 문서 정보 및 V1 기준

- **본 보고서:** 프로젝트 배포 환경에 대한 **총괄(거시) 보고서**이며, 모든 배포·인프라는 **V1 SSOT** 기준으로 운영된다.
- **실시간 상태:** 배포 후 Evidence·Drift는 `docs/00-SSOT/v1/reports/` 에서 확인한다.  
  - `audit.latest.md` — Batch CE/Queue, ASG, API, Build, SSM 등 Evidence  
  - `drift.latest.md` — SSOT 대비 실제 리소스 Drift  
  - `DEPLOY-TIMING-CHECKLIST.md` — 배포 지연/타임아웃 점검
- **배포 소요 시간:** Netprobe·콜드스타트 포함 **전체 25~30분** 여유 권장. (단계별 예상 시간은 `reports/DEPLOY-TIMING-CHECKLIST.md` 참고.)
- **Cursor/배포 룰:** `.cursor/rules/07_deployment_orchestrator.mdc` — 배포 시 참조.

---

## 1. V1 아키텍처 청사진 (Executive Summary)
현재 도입된 V1 아키텍처는 거시적으로 **"코드 푸시(GitHub) ➔ 빌드 및 이미지 레지스트리(AWS ECR) ➔ 오케스트레이션 및 파이프라인(AWS Batch/ASG) ➔ 전역 클라이언트 전송(Cloudflare R2/CDN)"** 으로 이어지는 구조를 띱니다.

- **CI/CD 흐름**: `main` 브랜치 푸시 시 GitHub Actions가 트리거되며, 멀티 스테이지 빌드 최적화가 적용된 Docker 이미지(베이스/API/메시징/AI/비디오 워커)가 ARM64(Graviton) 아키텍처로 빌드되어 AWS ECR에 푸시됩니다.
- **오케스트레이션**: `scripts/v1/deploy.ps1` 단일 진입점을 통해 모든 인프라 구성(ASG, ALB, Batch CE, EventBridge 등)이 절차적(Idempotent)으로 갱신되며, EC2, RDS, Redis 등의 백엔드 인프라가 배치를 주도합니다.
- **Stateless 구조 및 에지 전송**: 내부 인프라는 철저히 상태를 가지지 않도록(Stateless) 설계되었으며, 모든 미디어 및 정적 파일은 Cloudflare R2에 저장되고 Cloudflare CDN을 통해 사용자에게 전파됩니다.

## 2. 인프라 철학 및 SSOT(Single Source of Truth) 검증
본 프로젝트는 심각한 파편화를 방지하기 위해 엄격한 **SSOT(단일 진실 공급원) 철학**을 강제하고 있습니다.

- **SSOT 검증 성공적 구현**: `docs/00-SSOT/v1/params.yaml`을 유일한 인프라 구성 데이터 소스로 사용하고 있으며, 모든 스크립트는 이 파일만을 참조하여 인프라를 프로비저닝(`Ensure`)합니다.
- **레거시 실행 원천 차단(Guard)**: GitHub Actions 파이프라인 내부(`guard-no-legacy-scripts`)와 배포 스크립트 내부에서 레거시 스크립트(`scripts/infra/*`) 실행을 Denylist 기반으로 원천 차단하여 v1 시스템 우회를 금지하고 있습니다.
- **Drift 대응 인프라 정리**: `-PruneLegacy` 옵션을 통해 SSOT 명세에 존재하지 않는 인프라는 추적하여 제거함으로써 구성 드리프트(Configuration Drift) 문제를 깔끔하게 통제하고 있습니다.

## 3. 보안 및 인증 통합 수준 (Global Permission)
전역적인 권한 관리가 현대적인 클라우드 네이티브 방식으로 잘 구성되어 있습니다.

- **AWS OIDC 통합**: 빌드 및 배포 파이프라인에서 하드코딩된 장기(Long-term) 크리덴셜 대신 GitHub OIDC 기반(`aws-actions/configure-aws-credentials@v4`의 `role-to-assume`) 임시 토큰을 적극 도입하였습니다(Failover 시에만 Secret 사용).
- **Cloudflare 제어**: Workflow에서 Cloudflare API Token과 Wrangler를 활용해 CDN 캐시 갱신 및 R2 통제를 스크립트화하였습니다.
- **내부 워커 보안**: API 서버와 Video/AI/Messaging 워커 간 통신은 `INTERNAL_WORKER_TOKEN`을 통해 애플리케이션 레벨의 인증을 확보하고, RDS/Redis 패스워드는 SSM Parameter Store를 통해 암호화 주입(`SecureString`)됩니다.

## 4. 거시적 취약점 및 Action Items (핵심 과제 3가지)
현재 아키텍처의 철학(SSOT)과 확장성은 훌륭하나, 아키텍처 설계상 병목이 될 수 있는 **V2 도약을 위한 3대 핵심 갈아엎기 과제**를 제시합니다.

### 🔴 Action Item 1: Cloudflare 무효화(Purge) 방식의 위험성 제거
- **상태/취약점**: 배포 완료 직후 실행되는 스크립트(`video_batch_deploy.yml`)에서 `{"purge_everything":true}` 및 R2 버킷 전체 삭제(`--all`) 등 과격한 캐시 무효화가 사용되고 있습니다. 서비스가 확장될 경우 이러한 Blanket Purge 방식은 캐시 스탬피드(Cache Stampede) 및 원본 서버 OOM을 유발합니다.
- **해결 방안**: 배포 단위에서 불필요한 전체 삭제를 멈추고, SQS 및 EventBridge를 활용한 객체 단위(Granular) 타겟 변경 캐시 무효화 체계를 구축해야 합니다.

### 🔴 Action Item 2: 배포 스크립트 절차주의의 한계 (IaC 마이그레이션)
- **상태/취약점**: 훌륭한 배포 자동화(`deploy.ps1`) 스크립트지만, 절차적으로 진행되어 전체 실행 시간이 20~25분(Netprobe 및 콜드스타트 포함) 이상 소요됩니다. 셸 기반 제어는 스크립트가 비대해질수록 병렬 처리가 불가능해 배포/롤백 속도가 저하되는 근본 병목이 됩니다.
- **해결 방안**: 향후 V2에서는 Terraform 또는 AWS CDK와 같은 **선언적 IaC(Infrastructure as Code)**로 마이그레이션하여, 의존성 그래프 기반 병렬 리소스 생성 및 더욱 빠르고 안전한 상태(State) 관리를 달성해야 합니다.

### 🔴 Action Item 3: 데이터베이스의 Single Point of Failure (SPOF)
- **상태/취약점**: Worker, AI, API 모두 ASG 및 Batch 기반 다중화로 설계되었으나, `academy-v1-db`(RDS)와 `academy-v1-redis`는 단일 노드로 설정되어 있습니다. (현재 비용 최적화 사유로 파악됨)
- **해결 방안**: 프로덕션 규모가 커질 경우 읽기 레이블 분산(Read Replica) 및 Multi-AZ 이중화를 통한 Failover 구성을 필수 반영하여 수평 확장의 장점(Stateless 서버)을 뒷받침하는 데이터 티어 고가용성을 확보해야 합니다.
