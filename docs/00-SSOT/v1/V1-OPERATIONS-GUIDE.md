# V1 운영 가이드 (1인 개발·운영)

**대상:** 1,000~1,500 사용자 풀, 동시 50~300 버스트, 장애 대응 10~60분 지연 허용.  
**SSOT:** `docs/00-SSOT/v1/params.yaml`  
**배포:** `scripts/v1/deploy.ps1`  
**첫 배포:** V1 기준으로만 진행.

---

## 1. 설계 요약 (V1 기준)

| 영역 | 내용 | 비고 |
|------|------|------|
| **API** | ASG min/desired/max **2/2/4** | 최소 HA. 인스턴스 1대 장애 시에도 서비스 유지. |
| **API 롤링** | instance-refresh 시 **MinHealthyPercentage=100**, InstanceWarmup=300 | 새 인스턴스 healthy 후 구 인스턴스 제거로 무중단 배포. |
| **RDS** | Performance Insights 7일, Multi-AZ toggle | 커넥션/슬로우쿼리 관측 최소선. Multi-AZ는 비용 대비 필요 시 params `multiAz: true`. |
| **Django/워커** | CONN_MAX_AGE 기본 60 | 커넥션 폭증 방지(요청당 연결 재사용). |
| **SQS** | DLQ + VisibilityTimeout SSOT화 | Messaging 900s, AI 3600s. DLQ maxReceiveCount=5. Bootstrap에서 자동 적용. |
| **Video Batch** | 2-tier(standard/long), DDB lock(video_id), heartbeat 연장, READY만 노출 | 3시간 영상 정상 케이스, 1영상 1Job 보장, 유령데이터 방지. |
| **Observability** | 알람 threshold SSOT, 로그 30일 | API 5xx/Target Unhealthy, SQS/Batch/RDS/Redis 알람 파라미터 일원화. 10~60분 대응용(평가 5~15분). |

---

## 2. 운영 기본값 (params.yaml)

- **API:** asgMinSize=2, asgDesiredCapacity=2, asgMaxSize=4  
- **RDS:** performanceInsightsEnabled=true, performanceInsightsRetentionDays=7, multiAz=false  
- **SQS:** visibilityTimeoutSeconds(Messaging 900, AI 3600), dlqMaxReceiveCount=5  
- **Observability:** alarmPeriodSeconds=300, alarmEvaluationPeriods=2, logRetentionDays=30  

비용·안정성 균형을 위해 **기본값은 확장 전 상한만 열어두고**(예: API max=4, Batch standard maxvCpus=40, long maxvCpus=80), 실제 사용량은 알람·모니터링으로 제어.

---

## 3. 확장 시 스케일 업 경로 (SSOT 조정)

대규모 리팩토링 없이 params만 수정 후 재배포로 확장 가능.

| 항목 | 확장 방법 |
|------|-----------|
| **API** | asgMaxSize 4→6, asgDesiredCapacity 2→3 등. 인스턴스 타입 변경 시 api.instanceType 수정. |
| **RDS** | instanceClass 상향(db.t4g.medium → db.t4g.large). Multi-AZ 필요 시 multiAz: true. |
| **SQS 워커** | minSize/maxSize, scalingPolicyScaleOutThreshold 조정. Scale-in protection은 비용 위험 시 false 검토. |
| **Video Batch** | standard/long maxvCpus 상향. rootVolumeSizeGb 이미 200/300 반영. |
| **알람** | observability.* threshold 조정 후 알람 리소스 갱신(수동 또는 스크립트). |

---

## 4. 검증 시나리오 (최종 배포 필수)

| # | 시나리오 | 확인 방법 |
|---|----------|-----------|
| 1 | **API 롤링 배포 무중단** | deploy 시 LT 변경 후 instance-refresh. MinHealthyPercentage=100으로 새 인스턴스 healthy 될 때까지 구 인스턴스 유지. 배포 중에도 `/health` 200 유지 확인. |
| 2 | **3시간 영상 1건 완주** | long 큐/JobDef 사용, 12h timeout, heartbeat 45분. Job SUCCEEDED, Video READY, HLS 재생. |
| 3 | **동시 3건(standard/long 혼합)** | 3건 동시 제출 → 3 Job QUEUED→RUNNING→SUCCEEDED. Queue depth 알람 threshold 미만 유지. |
| 4 | **업로드 실패 후 업로드만 재시도** | R2 multipart 중단 시뮬레이션 → checkpoint 복구 후 재업로드만 수행(재인코딩 최소화). |
| 5 | **메시징 중복 방지 / DLQ** | 동일 idempotency key로 중복 발송 방지. 실패 메시지 maxReceiveCount 초과 시 DLQ 적재 확인. |
| 6 | **Evidence/Drift 최신화** | 배포 후 `scripts/v1/check-v1-infra.ps1` 실행 → drift.latest.md, audit.latest.md 갱신. |

---

## 5. 1인 운영 시 주의사항

- **알람:** CloudWatch 알람은 SSOT에 threshold만 정의. 실제 알람 리소스 생성은 수동 또는 별도 스크립트(Ensure-V1Alarms)로 수행. SNS 토픽 연동 시 10~60분 내 대응 가능.
- **RDS:** Performance Insights 7일 보존으로 비용 최소. 슬로우 쿼리 로그는 RDS 파라미터 그룹에서 필요 시 활성화.
- **비용:** API 2대 상시, Batch maxvCpus 40+80 상한만 열어두고 사용량 기반 과금. 알람으로 queue depth / failed jobs 모니터링 권장.

---

## 6. ECR / SSM / 로그 표준화

| 항목 | 내용 |
|------|------|
| **ECR** | lifecycle: `docs/00-SSOT/v1/scripts/ecr-lifecycle-policy.json`. 태그 v1-, bootstrap- 최신 20개 유지, untagged 1일 만료. Ensure-ECRRepos에서 전체 repo 일괄 적용. immutable tag 필수(params ecr.immutableTagRequired). |
| **SSM** | `/academy/api/env`, `/academy/workers/env` 구조 일관. workers 필수 키: AWS_DEFAULT_REGION, DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT, R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_VIDEO_BUCKET, API_BASE_URL, INTERNAL_WORKER_TOKEN, REDIS_HOST, REDIS_PORT, DJANGO_SETTINGS_MODULE. `/academy/rds/master_password`는 Prune 제외(SSOT 보호). |
| **로그** | retention 30일. Batch 로그 그룹 `/aws/batch/academy-video-worker`, `/aws/batch/academy-video-ops`에 put-retention-policy 적용(Ensure-VideoBatchLogRetention). observability.logRetentionDays SSOT. |
| **보안** | SG 인바운드 최소화(params network.securityGroupApp 등). IAM 최소 권한(R2/SSM read 범위 점검). SSH 대신 SSM Session Manager 우선. |

---

**문서 끝.**
