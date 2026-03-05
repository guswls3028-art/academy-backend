# V1 Deployment Verification Report

**명칭:** V1 통일 (V1.1 미사용). **SSOT:** docs/00-SSOT/v1/params.yaml. **리전:** ap-northeast-2. **전제:** 사용자 1,000~1,500, 동시 50~300 버스트, 운영 1인, 장애 대응 10~60분.

## 배포 정보
| 항목 | 값 |
|------|-----|
| 검증 시각 | 2026-03-06T04:56:03.8545601+09:00 |
| 리전 | ap-northeast-2 |
| 배포 스크립트 | scripts/v1/deploy.ps1 |
| 근거·로그 | reports/audit.latest.md, reports/drift.latest.md |

---

## 1) 인프라 상태 (PASS/WARNING/FAIL + 근거)

| 항목 | 결과 | 근거(로그/지표/스크린샷 경로) |
|------|------|-------------------------------------|
| API ASG min/desired/max | 2/2/4 | reports/audit.latest.md (apiAsg*) |
| ALB target health | 0 / 3 healthy | AWS Console EC2 > Target Groups > academy-v1-api-tg |
| /health 200 | unreachable  | curl 위 URL 또는 ALB DNS 직접 호출 |
| AI/Messaging ASG | 1/1 | reports/audit.latest.md (asgAi*, asgMessaging*) |
| SQS queue 연결·DLQ | Messaging depth error DLQ 0 / AI depth error DLQ 0 | SQS Console 또는 get-queue-attributes |
| Video Batch CE/Queue/JobDef | CE VALID Queue ENABLED JobDef rev 11 | reports/audit.latest.md, Batch Console |
| RDS 연결 가능 | available | RDS describe-db-instances (연결 테스트는 앱/psql 수동) |
| Redis 연결 가능 | available | ElastiCache describe-replication-groups |
| **섹션 1 종합** | **FAIL** | |

## 2) 기능 Smoke Test (PASS/WARNING/FAIL + 근거)

| 항목 | 결과 | 근거 |
|------|------|------|
| /health | unreachable | 응답시간:  (기준 p95 &lt; 2s, 샘플 1회) |
| API root | root unreachable | 동일 ALB DNS |
| 핵심 API 1~2개(인증/CRUD) | 수동 검증 권장 | 샘플 20회 평균/최대 기록 시 reports/ 에 URL 또는 로그 경로 기입 |
| **섹션 2 종합** | **FAIL** | |

## 3) 프론트 / R2 / CDN (PASS/WARNING/FAIL + 근거)

| 항목 | 결과 | 근거 |
|------|------|------|
| 프론트 URL 접속 | not checked | FRONT_APP_URL env 설정 시 자동 검사 |
| 정적 자산(JS/CSS) 로딩 | 수동 검증 권장 | 브라우저 개발자도구 Network 탭 |
| CDN 캐시 정책 | 수동 검증 권장 | Cache-Control 헤더, 배포 시 purge 전략 (params front.*) |
| 프론트→API(CORS/쿠키/CSRF) | 수동 검증 권장 | 동일 도메인/credentials 요청 |
| R2 버킷 접근 | OK (wrangler list success) | wrangler r2 bucket list |
| **섹션 3 종합** | **WARNING** | |

## 4) SQS 워커 테스트 (PASS/WARNING/FAIL + 근거)

| 항목 | 결과 | 근거 |
|------|------|------|
| AI queue enqueue→consume | 수동 검증 권장 | SQS 메시지 발송 후 워커 로그 확인 |
| Messaging queue enqueue→consume | 수동 검증 권장 | 동일 |
| DLQ 적재 없음 | Messaging DLQ=0 AI DLQ=0 | get-queue-attributes ApproximateNumberOfMessages (DLQ) |
| **섹션 4 종합** | **PASS** | |

## 5) Video Pipeline 테스트 (3시간 영상 기준)

| 항목 | 결과 | 근거 |
|------|------|------|
| 3시간 샘플 1건 end-to-end | 수동 검증 권장 | 인코딩→R2 staging→검증→READY, HLS 재생 |
| 유령데이터 방지(READY 전 미공개) | 설계 반영 | API playback_mixin READY만 허용, 목록 READY 필터 |
| 업로드 실패 재시도/복구 | 설계 반영 | DynamoDB checkpoint, 재인코딩 최소화 (V1-DEPLOYMENT-VERIFICATION §7.3) |
| 동시 2~3건 | 수동 검증 권장 | 2~3건 동시 제출 후 Job 완료·queue depth 확인 |
| **섹션 5 종합** | **WARNING** | 수동 검증 권장: 3시간 샘플 1건 end-to-end, READY 전 미공개, 업로드 재시도·동시 2~3건. 근거: deploy-verification-latest.md 또는 수동 실행 로그. |

## 6) 관측/알람

| 항목 | 결과 | 근거 |
|------|------|------|
| 최소 알람 세트(API 5XX, SQS depth/DLQ, Batch failed/stuck/backlog, RDS, Redis) | 5 alarms (academy/v1) | CloudWatch > Alarms (academy/v1 필터) |
| 로그 retention 30d | params observability.logRetentionDays | Ensure-VideoBatchLogRetention, Batch 로그 그룹 |
| **섹션 6 종합** | **PASS** | |

## 7) 리스크 및 GO/NO-GO 권고

### 발견 사항(리스크)
- **WARNING** [Drift] SSOT와 불일치 1건: API LT/academy-v1-api-lt
- **FAIL** [API] /health unreachable: The request was canceled due to the configured HttpClient.Timeout of 10 seconds elapsing.
- **FAIL** [API] ALB target healthy 0 / 3

### RCA 및 조치 요약
- **확정 원인:** sg-app 8000 인바운드가 10.0.0.0/16만 있어 VPC 172.30.0.0/16 ALB→EC2 차단(Target.Timeout) + academy-api 컨테이너 미기동.  
- **조치 반영:** resources/network.ps1 — sg-app에 8000 from SSOT VpcCidr(172.30.0.0/16) 추가. 조치 후 Target는 FailedHealthChecks(연결 성공·앱 미응답).  
- **상세:** [rca.latest.md](./rca.latest.md)

### GO/NO-GO
| 판정 | 내용 |
|------|------|
| **NO-GO** | FAIL 항목 해결 후 재검증 필요. |

- **FAIL 1건 이상** → **NO-GO**. 재검증 후 재실행.
- **WARNING만** → **CONDITIONAL GO**. 영향도·완화책·추적 계획 확인 후 배포 여부 결정.
- **PASS만** → **GO**.

---

## 최종 상태
**FAIL**

**연관 보고서:** audit.latest.md, drift.latest.md, rca.latest.md (동시 갱신됨).

