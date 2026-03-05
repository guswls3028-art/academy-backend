# SSOT 멱등성 규칙 — 원테이크 보장

**역할:** Describe → Decision → Update/Create 순서와 리소스별 멱등 동작 규칙. 동일 절차 10회 실행 시 최종 상태 동일.

---

## 전제

- 모든 리소스는 **(name + tag set)**으로 유일 식별. SSOT-RESOURCE-INVENTORY에 정의.
- 배포는 **Describe → Decision(Plan Artifact 생성 권장) → Update/Create** 순서만 수행.
- **동시 실행 방지:** 락이 없으면 멱등성 보장 불가. 락 전략(예: DynamoDB/S3/GitHub 환경 락)을 도입 시 문서에 명시. (현재: **확인 필요** — 미구현 시 동시 실행 금지로 운영.)

---

## AWS Batch Compute Environment

- **Describe:** `aws batch describe-compute-environments --compute-environments <CE_NAME> --region <REGION>`
- **Decision:**
  - `status == "VALID"` && `state == "ENABLED"` → 변경 없음.
  - `status == "INVALID"` → Update 시도 금지. 아래 재생성 루틴 수행.
- **Update/Create(INVALID 시):**
  1. Job Queue에서 해당 CE 분리(update-job-queue, computeEnvironmentOrder에서 제거).
  2. CE DISABLED로 변경(update-compute-environment --state DISABLED).
  3. CE 삭제(delete-compute-environment). **Wait:** describe-compute-environments until 삭제 완료(타임아웃 예: 300초).
  4. 동일 이름으로 CE 재생성(create-compute-environment, SSOT 스펙 준수).
  5. **Wait loop:** describe-compute-environments 반복 until `status == "VALID"` and `state == "ENABLED"`. 최대 대기 시간 명시(예: 600초). 초과 시 FAIL.
  6. Job Queue에 CE 다시 연결(update-job-queue, computeEnvironmentOrder 복원).
- **멱등:** 이미 VALID/ENABLED이고 스펙이 SSOT와 같으면 아무 작업 안 함.

---

## AWS Batch Job Queue

- **Describe:** `aws batch describe-job-queues --job-queues <QUEUE_NAME> --region <REGION>`
- **Decision:** state=ENABLED, computeEnvironmentOrder에 CE 1개만(SSOT대로)인지 확인.
- **Update/Create:** CE 순서/연결만 바꿀 때 update-job-queue. Queue 자체를 삭제 후 재생성하는 것은 RUNNING/RUNNABLE job 없을 때만(CleanupOld 시나리오).

---

## AWS Batch Job Definition

- **Describe:** `aws batch describe-job-definitions --job-definition-name <NAME> --status ACTIVE --region <REGION>`
- **Decision:** 최신 revision의 vcpus, memory, image, retryStrategy, timeout이 SSOT와 일치하는지 비교.
- **Update/Create:** **vCPU/Memory/Image 변경 시에만** register-job-definition로 새 revision 등록. 동일 스펙이면 기존 ACTIVE revision 재사용(submit 시 이름만 사용, revision 하드코딩 금지).
- **이미지:** immutable tag 필수. `:latest` 사용 시 원테이크는 FAIL.

---

## ASG (Auto Scaling Group)

- **Describe:** `aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names <NAME> --region <REGION>`
- **Decision:** Launch Template 버전이 의도한 버전인지, DesiredCapacity가 0으로 초기화되어 있지 않은지 확인.
- **Update/Create:**
  - Launch Template 변경 시: 새 버전 생성 후 ASG를 새 Launch Template 버전으로만 업데이트.
  - **Desired Capacity:** 현재값 유지. Update 시 **Desired Capacity를 0으로 설정하지 않음.** (현재값 유지 규칙)
- **멱등:** 동일 Launch Template 버전·동일 min/max이면 update 없음.

---

## EventBridge Rule

- **Describe:** `aws events describe-rule --name <RULE_NAME> --region <REGION>`, `aws events list-targets-by-rule --rule <RULE_NAME> --region <REGION>`
- **Decision:** ScheduleExpression 일치, Target이 Ops Queue(또는 SSOT에 정의된 대상)인지 확인.
- **Update/Create:** Rule이 이미 있으면 **Target만** put-targets로 최신화. Rule 삭제 후 재생성 금지. Enable/Disable만 enable-rule/disable-rule로 제어.
- **멱등:** Rule 존재 시 put-targets만 반복해도 최종 타깃 상태 동일.

---

## SSM Parameter

- **Describe:** `aws ssm get-parameter --name /academy/workers/env --region <REGION> --query Parameter.Value --output text` (값 검증 시 디코딩)
- **Decision:** 필수 키 존재 여부(SSM_JSON_SCHEMA). 값 변경 필요 시에만 put-parameter.
- **Update/Create:** `.env` → `ssm_bootstrap_video_worker.ps1`로만 갱신. 콘솔 수동 편집 금지.
- **멱등:** 동일 .env 기준으로 재실행 시 같은 ParameterVersion 또는 증가만.

---

## 상태 전이 Wait 루프 요약

| 리소스 | 대기 조건 | 타임아웃(권장) |
|--------|-----------|------------------|
| CE 삭제 후 | describe-compute-environments에서 해당 CE 없음 | 300초 |
| CE 생성 후 | status=VALID, state=ENABLED | 600초 |
| Netprobe Job | status=SUCCEEDED (또는 FAILED) | 300초, RUNNABLE 정체 시 180초 등으로 별도 설정 |

---

## SSOT 기준 (충돌 시 결정 규칙)

1. **최우선:** IaC(Terraform/CloudFormation/Helm/Kustomize) — 현재 레포에는 없음.
2. **다음:** CI/CD 파이프라인(.github/workflows). 경로·이름은 스크립트와 일치해야 함.
3. **다음:** 배포 스크립트(scripts/, Makefile). **코드가 진실.**
4. **마지막:** 문서(docs/). 충돌 시 문서는 코드에 맞춰 수정.
5. 코드끼리 충돌 시: 추측하지 말고 "확인 필요"로 표시하고, 확인 명령(aws cli, describe-* 등) 제시.

---

## 스크립트 인터페이스 (문서 정의)

배포 스크립트가 지원하면 좋은 옵션:

- **--env:** 환경(prod/staging). 기본 prod.
- **--dry-run:** Describe·Decision만, 변경 없음.
- **--plan:** Plan 아티팩트(변경 목록) 생성만.
- **--apply:** Update/Create 실행.
- **--lock:** 동시 실행 방지(락). 미구현 시 문서에 "동시 실행 금지" 명시.
- **--verbose:** 상세 로그.

현재 원테이크: `-FixMode`, `-EnableSchedulers` 등으로 제어. 위 옵션은 통합 스크립트에서 적용 권장.
