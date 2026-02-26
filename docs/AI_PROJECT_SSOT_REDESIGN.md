# AI 프로젝트 문서 체계 재설계 — SSOT 원칙

**역할:** AI 플랫폼 아키텍트 / MLOps 리드  
**목표:** 데이터·모델·실험·프롬프트·평가·배포 분리, 실험 vs 확정 구분, 모델/문서 버전 정합성, 재현 가능 구조, 신규 팀원 1시간 내 구조 이해 가능

---

# 1단계: 현재 문서 진단

## 1.1 전체 문서 트리 (요약)

```
docs/
├── README.md
├── REFERENCE.md              # 백엔드 참조 SSOT
├── 배포.md, 운영.md, 설계.md, 10K_기준.md, 30K_기준.md
├── ai/
│   └── AI_HANDOFF_CONTEXT.md
├── AI_BATCH_WORKER_VS_OPS.md
├── video/
│   ├── batch/                # VIDEO_BATCH_*.md (설계·검증·체크리스트·리포트)
│   ├── worker/                # VIDEO_WORKER_*.md, VIDEO_WORKER_SCALING_SSOT.md
│   └── legacy/                # VIDEO_ENTERPRISE_*, B1_*, VIDEO_*.md
├── infra/                    # API_ENV_*, LAMBDA_*, STRICT_VERIFICATION_GPT_PROMPT.md
├── deploy/
│   ├── VIDEO_WORKER_INFRA_SSOT_V1.md
│   ├── VIDEO_WORKER_INFRA_SSOT_v1_1.md
│   ├── VIDEO_WORKER_INFRA_SSOT_PUBLIC_V2.md
│   ├── VIDEO_INFRA_ONE_TAKE_ORDER.md, SSM_JSON_SCHEMA.md
│   ├── actual_state/, audit_reports/
│   └── 기타 배포·수정·리포트 md
├── archive/
│   ├── cursor_legacy/         # MASTER_PROMPT_*, db_load_*, verification 등
│   ├── 0216/, SSOT_0217/, SSOT_0218/
│   └── README.md
├── adr/                      # ADR-001~004, admin API 편집
├── (루트 다수)                # VIDEO_*_REPORT.md, PRODUCTION_*.md, FORENSIC_*.md 등
├── video_batch_production_runbook.md
├── INFRA_VERIFICATION_SCRIPTS.md
└── 기타 산발 md
```

## 1.2 실험성 문서 vs 확정 문서 구분

| 분류 | 위치/패턴 | 예시 |
|------|-----------|------|
| **확정(스펙/SSOT)** | deploy/ SSOT 문서, REFERENCE.md, runbook, SSM_JSON_SCHEMA | VIDEO_WORKER_INFRA_SSOT_*.md, video_batch_production_runbook.md, REFERENCE.md |
| **실험/조사/리포트** | FORENSIC_*, *_REPORT.md, *_AUDIT*.md, *_VERIFICATION*, *_INVESTIGATION* | FORENSIC_AUDIT_*, VIDEO_BATCH_DESIGN_VERIFICATION_REPORT, PRODUCTION_READINESS_AUDIT_FACTUAL |
| **일회성 배포/수정 기록** | deploy/ 내 FIX, CHANGELOG, ONE_TAKE | VIDEO_BATCH_ONE_TAKE_REFLECT_FIX, PRODUCTION_ONE_SHOT_CHANGELOG |
| **레거시/참고** | video/legacy/, archive/ | VIDEO_ENTERPRISE_*, B1_*, cursor_legacy |

**문제:** 실험·검증 리포트와 확정 스펙이 같은 디렉터리(deploy/, video/batch)에 섞여 있음. “확정”이라고 표시된 SSOT도 파일이 3종(V1, v1_1, PUBLIC_V2) 존재.

## 1.3 중복 정의된 모델 스펙 탐지

| 주제 | 문서 | 충돌/중복 내용 |
|------|------|----------------|
| **Video Worker 인프라** | VIDEO_WORKER_INFRA_SSOT_V1.md, VIDEO_WORKER_INFRA_SSOT_v1_1.md, VIDEO_WORKER_INFRA_SSOT_PUBLIC_V2.md | 동일 “프로덕션 SSOT”을 3개 파일이 주장. README는 V1 링크, 일부 문서는 v1_1/V2 참조. |
| **Video CE 이름** | VIDEO_WORKER_SCALING_SSOT.md vs deploy SSOT | SCALING_SSOT: `academy-video-batch-ce-v3` / deploy SSOT: `academy-video-batch-ce-final` → CE 이름 불일치. |
| **Batch 워커 vs Ops** | AI_BATCH_WORKER_VS_OPS.md vs deploy SSOT | AI 문서: CE `academy-video-batch-ce-v2` (또는 v3) / SSOT: `academy-video-batch-ce-final` → 버전 표기 불일치. |

**결론:** 인프라 “모델 스펙”에 해당하는 리소스 이름·스펙의 SSOT가 여러 파일에 분산되어 있으며, 서로 다른 CE 이름이 혼재함.

## 1.4 프롬프트 버전 분산 여부

| 문서 | 성격 | 위치 |
|------|------|------|
| AI_HANDOFF_CONTEXT.md | AI 맥락 전달용(추측 금지·확정 사실) | docs/ai/ |
| AI_BATCH_WORKER_VS_OPS.md | Batch 워커 vs Ops 구분용 프롬프트 조각 | docs/ 루트 |
| STRICT_VERIFICATION_GPT_PROMPT.md | GPT 검증 절차·체크리스트 | docs/infra/ |
| MASTER_PROMPT_DB_LOAD_REDUCTION.md | DB 부하 0 설계용 마스터 프롬프트 | docs/archive/cursor_legacy/ |

**문제:** “프롬프트”에 대한 단일 SSOT 없음. 운영용 시스템 프롬프트와 실험/검증용 프롬프트가 혼재하며, 버전/이력 관리 체계 없음.

## 1.5 데이터 정의 중복 여부

| 주제 | 문서 | 비고 |
|------|------|------|
| SSM Worker env | deploy/SSM_JSON_SCHEMA.md, video_batch_production_runbook.md | SSOT는 SSM_JSON_SCHEMA. runbook에서 반복 설명. |
| DB/스키마 | PRODUCTION_READINESS_AUDIT_FACTUAL.md, VIDEO_BATCH_DESIGN_VERIFICATION_REPORT.md | “DB schema” 일부 기술, 전용 dataset_spec/스키마 SSOT 없음. |
| B1 메트릭 | B1_METRIC_SCHEMA_EXTRACTION_REPORT.md, B1_IMPLEMENTATION_FINAL_REPORT.md | legacy 내 메트릭 스키마·구현 리포트만 존재. |
| 엑셀 파싱 | cursor_legacy 12, 13번, excel_schema_infer 코드 | 스키마/컬럼 정의가 문서와 코드에 분산. |

**결론:** 데이터/스키마/계약에 대한 단일 정의 위치가 없고, 인프라(SSM)와 레거시(B1/엑셀)가 서로 다른 곳에 흩어져 있음.

---

## 현재 문제 요약

### 구조 문제

- 실험·검증·리포트와 확정 스펙·런북이 같은 폴더에 혼재(deploy/, video/batch, 루트).
- AI/프롬프트 전용 계층은 `ai/` 한 폴더뿐이며, 실험·프롬프트 버전·평가 지표·모델 스펙이 분리되어 있지 않음.
- “인프라/비디오” 중심 구조라, 데이터·모델·실험·평가·프롬프트를 일관된 번호 체계로 찾기 어려움.

### SSOT 위반 지점

- **모델(인프라) 스펙:** VIDEO_WORKER 인프라 SSOT가 V1 / v1_1 / PUBLIC_V2 세 파일로 나뉘어 있고, README·다른 문서가 서로 다른 파일을 참조.
- **리소스 이름:** CE 이름이 `ce-final` vs `ce-v2`/`ce-v3`로 문서별로 상이.
- **프롬프트:** 운영용·검증용·설계용 프롬프트가 ai/, infra/, archive에 분산되어 단일 SSOT 없음.
- **데이터/계약:** SSM 스키마만 deploy에 명시되어 있고, 데이터셋/피처/평가용 데이터 정의는 일원화되지 않음.

### 위험 요소

- 신규 팀원이 “어느 문서가 지금 프로덕션 기준인가?”를 1시간 내에 파악하기 어려움.
- 실험 결과를 스펙 문서에 직접 반영할 유혹이 있어, 스펙 오염 위험.
- 프롬프트 변경 시 영향 범위·버전이 문서로 정의되어 있지 않음.
- 모델(또는 인프라 스펙) 버전과 문서 버전 동기화 규칙이 없음.

---

# 2단계: AI 프로젝트 전용 SSOT 폴더 설계

아래는 **AI/ML 문서**와 **기존 인프라·비디오 문서**를 함께 수용하는 상위 구조이다. 기존 `docs/` 내용은 이 번호 체계에 맞춰 이동·참조한다.

```
docs/
├── 00_strategy          # AI/제품 전략, 목표, KPI 정의 (1~2줄: 전략·로드맵 요약)
├── 01_product          # 제품 요구사항·기능 명세 (1~2줄: 제품-도메인 연결)
├── 02_domain           # 도메인 용어·비즈니스 규칙 (1~2줄: 도메인 SSOT)
├── 03_data
│   ├── dataset_specs   # 데이터셋 명세(이름, 버전, 스키마, 출처). 데이터셋 SSOT.
│   ├── feature_definitions  # 피처 정의·파생 규칙. 피처 SSOT.
│   └── data_contracts  # API/파이프라인 간 데이터 계약(입출력 스키마). 계약 SSOT.
├── 04_model
│   ├── model_specs     # 모델(또는 인프라 리소스) 스펙 단일 SSOT. 확정 스펙만.
│   ├── model_versions  # 버전별 변경 이력·태그. 모델-문서 버전 대응표.
│   └── training_config  # 학습 설정(하이퍼파라미터 등) 확정본. 실험 설정 아님.
├── 05_experiment
│   ├── hypothesis     # 실험 가설·목표·설계. 실험 계획 SSOT.
│   ├── experiment_logs  # 실험 로그(날짜, 설정, 메트릭 요약). 실험 기록만.
│   └── results        # 실험 결과 요약·결론. 스펙에 직접 반영 금지.
├── 06_prompt
│   ├── system         # 운영용 시스템/역할 프롬프트. 프롬프트 SSOT.
│   ├── evaluation_prompts  # 평가용 프롬프트(벤치마크·검증). 평가 전용.
│   └── archived_versions  # 이전 버전 프롬프트 보관. 수정 금지.
├── 07_evaluation
│   ├── metrics_definition  # 지표 정의·계산 방식·해석. 평가 기준 SSOT. 여기서만 수정.
│   └── benchmark_results   # 벤치마크 결과·대시보드 링크. 결과만 저장.
├── 08_architecture
│   ├── serving        # 서빙 아키텍처·API·스케일. 서빙 SSOT.
│   └── pipeline       # 학습/추론 파이프라인 흐름. 파이프라인 SSOT.
├── 09_infra            # 인프라 리소스·네트워크·배포 (기존 deploy/infra 통합 참조)
├── 10_ops              # 런북·장애 대응·체크리스트 (기존 runbook 등)
├── 11_adr               # Architecture Decision Records (기존 adr 정리)
└── 99_archive          # 폐기/과거 스냅샷 (기존 archive 매핑)
```

## 폴더별 역할 (1~2줄)

| 폴더 | 역할 |
|------|------|
| **00_strategy** | AI/제품 전략·로드맵·성공 지표 요약. “무엇을 왜 하는가”의 단일 참조. |
| **01_product** | 제품 요구사항·기능 명세. 도메인-기능 매핑. |
| **02_domain** | 도메인 용어·비즈니스 규칙. 용어집·규칙 SSOT. |
| **03_data/dataset_specs** | 데이터셋 이름·버전·스키마·출처. 데이터셋 SSOT. |
| **03_data/feature_definitions** | 피처 정의·파생 규칙. 피처 SSOT. |
| **03_data/data_contracts** | API/배치 간 입출력 스키마·계약. 계약 SSOT. |
| **04_model/model_specs** | 모델(및 인프라 리소스) 확정 스펙. **모델 스펙은 여기만 SSOT.** |
| **04_model/model_versions** | 버전별 변경 이력·태그. 모델 버전 ↔ 문서 버전 대응. |
| **04_model/training_config** | 학습 설정 확정본(재현용). 실험용 설정은 05_experiment. |
| **05_experiment/hypothesis** | 실험 가설·목표·설계. 실험 계획 SSOT. |
| **05_experiment/experiment_logs** | 실험 로그(날짜, 설정, 메트릭 요약). **실험 기록은 여기까지.** |
| **05_experiment/results** | 실험 결과 요약·결론. 스펙 문서에 직접 반영 금지. |
| **06_prompt/system** | 운영용 시스템/역할 프롬프트. **프롬프트 운영 버전 SSOT는 여기.** |
| **06_prompt/evaluation_prompts** | 평가·검증용 프롬프트. 운영과 분리. |
| **06_prompt/archived_versions** | 이전 버전 프롬프트 보관. 수정 금지. |
| **07_evaluation/metrics_definition** | 지표 정의·계산·해석. **평가 기준은 여기서만 수정 가능.** |
| **07_evaluation/benchmark_results** | 벤치마크 결과만 저장. 지표 정의는 metrics_definition만 참조. |
| **08_architecture/serving** | 서빙 아키텍처·API·스케일. 서빙 SSOT. |
| **08_architecture/pipeline** | 학습/추론 파이프라인. 파이프라인 SSOT. |
| **09_infra** | 인프라 리소스·네트워크·배포. 기존 deploy/infra 내용 참조·이동. |
| **10_ops** | 런북·장애 대응·체크리스트. 기존 runbook·검증 스크립트 문서. |
| **11_adr** | ADR. 결정 이력. |
| **99_archive** | 폐기·과거 스냅샷. 읽기 전용. |

## 핵심 질문에 대한 답

- **실험 기록은 어디까지 남길 것인가?**  
  → **05_experiment/** 한계. hypothesis(계획), experiment_logs(로그), results(결과 요약). 그 외 폴더에는 실험 로그를 두지 않는다.

- **확정 모델 스펙은 어디에 두는가?**  
  → **04_model/model_specs.** 인프라 리소스 스펙(예: Video Worker CE/Queue/JobDef)도 “모델”에 준하여 여기 또는 09_infra에 단일 문서로 둔다.

- **프롬프트 SSOT는 어디인가?**  
  → **06_prompt/system.** 운영에 쓰는 시스템/역할 프롬프트는 이 디렉터리만 SSOT. 변경 시 archived_versions로 이전 버전 이동 후 수정.

- **평가 기준은 어디서만 수정 가능한가?**  
  → **07_evaluation/metrics_definition.** 지표 정의·계산·해석은 이곳에서만 수정. benchmark_results는 결과만 기록.

---

# 3단계: 문서 이동 매핑표

| 기존 문서 | 새 위치 | 분류(실험/확정) | 삭제 여부 | 이유 |
|-----------|---------|------------------|-----------|------|
| deploy/VIDEO_WORKER_INFRA_SSOT_V1.md | 04_model/model_specs/ 또는 09_infra/ (단일 SSOT로 통합 후) | 확정 | 삭제 아님, 통합 후 1개만 유지 | 인프라 스펙 SSOT 단일화. V1/v1_1/V2 중 확정 1개만 남기고 나머지는 99_archive로. |
| deploy/VIDEO_WORKER_INFRA_SSOT_v1_1.md | 99_archive/ (또는 통합본 반영 후 삭제) | 확정(이력) | 통합 후 보관 또는 삭제 | SSOT 단일화 후 이력만 보관. |
| deploy/VIDEO_WORKER_INFRA_SSOT_PUBLIC_V2.md | 99_archive/ 또는 04_model/model_specs (현행이면 통합본에 반영) | 확정 | 통합 후 보관 | 동일. |
| deploy/SSM_JSON_SCHEMA.md | 03_data/data_contracts/SSM_workers_env.md | 확정 | 삭제 아님 | 데이터 계약 SSOT. |
| deploy/VIDEO_INFRA_ONE_TAKE_ORDER.md | 10_ops/ 또는 09_infra/ | 확정 | 삭제 아님 | 배포/운영 절차. |
| video_batch_production_runbook.md | 10_ops/video_batch_runbook.md | 확정 | 삭제 아님 | 런북. |
| REFERENCE.md | 02_domain/ 또는 유지(상위 참조) | 확정 | 삭제 아님 | 백엔드/코어 참조. |
| ai/AI_HANDOFF_CONTEXT.md | 06_prompt/system/ai_handoff_context.md | 확정 | 삭제 아님 | AI 맥락 프롬프트 SSOT. |
| AI_BATCH_WORKER_VS_OPS.md | 06_prompt/system/batch_worker_vs_ops.md (또는 09_infra 참조 문서) | 확정 | 삭제 아님 | 프롬프트 조각 통합. |
| infra/STRICT_VERIFICATION_GPT_PROMPT.md | 06_prompt/evaluation_prompts/ 또는 10_ops/ 검증 체크리스트 | 확정 | 삭제 아님 | 검증용 프롬프트/체크리스트. |
| archive/cursor_legacy/MASTER_PROMPT_DB_LOAD_REDUCTION.md | 06_prompt/archived_versions/ | 실험/과거 | 삭제 아님 | 이전 설계 프롬프트 보관. |
| video/worker/VIDEO_WORKER_SCALING_SSOT.md | 04_model/model_specs/ 참조 통합 또는 09_infra/ (CE 이름 SSOT와 일치시킨 후) | 확정 | 삭제 아님 | 스케일링 스펙을 단일 model_specs와 정합. |
| video/worker/VIDEO_WORKER_ARCHITECTURE_BATCH.md | 08_architecture/pipeline/ 또는 09_infra/ | 확정 | 삭제 아님 | 아키텍처. |
| video/batch/VIDEO_BATCH_*_REPORT.md, *_VERIFICATION*.md, *_AUDIT*.md | 05_experiment/results/ 또는 99_archive/ | 실험 | 삭제 아님 | 실험/검증 결과. |
| video/batch/VIDEO_BATCH_REFACTOR_PLAN*, CHECKLIST, DESIGN_* | 05_experiment/hypothesis 또는 10_ops/ (목적에 따라) | 혼합 | 삭제 아님 | 계획·체크리스트. |
| video/legacy/* | 99_archive/video_legacy/ | 실험/레거시 | 삭제 아님 | 참고용. |
| docs 루트 FORENSIC_*, *_REPORT.md, PRODUCTION_READINESS_AUDIT* | 05_experiment/results/ 또는 99_archive/ | 실험 | 삭제 아님 | 조사·감사 결과. |
| deploy/actual_state/, audit_reports/ | 09_infra/ 또는 10_ops/ (경로만 정리) | 확정(상태) | 삭제 아님 | 실제 상태 스냅샷. |
| adr/* | 11_adr/ | 확정 | 삭제 아님 | ADR 유지. |
| archive/* | 99_archive/ (하위 구조 유지 가능) | 아카이브 | 삭제 아님 | 과거 스냅샷. |
| PRODUCTION_*_FIX*.md, *_CHANGELOG.md | 10_ops/ 또는 99_archive/ | 일회성 | 보관 후 정리 | 변경 이력. |

---

# 4단계: AI 전용 SSOT 규칙 정의

1. **모델 스펙**  
   - **04_model/model_specs**만 모델(및 인프라 리소스) 스펙의 SSOT이다.  
   - 다른 문서는 “model_specs의 문서명·섹션”을 참조만 하고, 스펙 값을 중복 정의하지 않는다.  
   - Video Worker 인프라의 경우, CE/Queue/JobDef 이름·스펙은 **단일 파일 하나**만 SSOT로 두고, 나머지 SSOT 후보 문서는 99_archive로 이동하거나 통합본에 흡수한다.

2. **프롬프트 운영 버전**  
   - **06_prompt/system**만 운영용 시스템/역할 프롬프트의 SSOT이다.  
   - 배포·자동화에 쓰는 프롬프트는 모두 이 디렉터리에서 참조한다.  
   - 변경 시: 기존 파일을 **06_prompt/archived_versions/**로 복사(날짜/버전 접미사 부여)한 뒤, system에서만 수정한다.

3. **평가 지표 정의**  
   - **07_evaluation/metrics_definition**만 지표 정의·계산 방식·해석을 수정할 수 있다.  
   - 실험 결과·벤치마크 결과는 **07_evaluation/benchmark_results**에만 기록하며, metrics_definition 파일 자체를 실험 결과로 덮어쓰지 않는다.

4. **실험 결과와 스펙 분리**  
   - 실험 결과는 **05_experiment/results**(및 experiment_logs)에만 기록한다.  
   - **04_model/model_specs**, **07_evaluation/metrics_definition**, **03_data/** 계약 문서에는 실험 결과를 직접 반영하지 않는다.  
   - 스펙 반영은 별도 PR/체크리스트로 “실험 결과 → 스펙 반영” 단계를 거친다.

5. **모델 버전과 문서 버전 동기화**  
   - **04_model/model_versions**에 “모델(또는 인프라) 버전 ↔ 문서 버전” 대응표를 유지한다.  
   - 규칙: 배포/릴리스 시 model_versions에 (배포 버전, 적용된 model_specs 문서/해시 또는 경로, 적용일)을 추가한다.  
   - “현재 프로덕션”은 model_versions에서 한 행으로 식별 가능해야 한다.

---

# 5단계: 거버넌스 규칙 정의

## 5.1 새 실험 시작 시 생성해야 할 문서

- **05_experiment/hypothesis/**  
  - `YYYYMMDD_<실험명>_hypothesis.md`: 가설, 목표, 사용 데이터/모델 버전, 평가 지표(07_evaluation/metrics_definition 참조), 성공 기준.
- **05_experiment/experiment_logs/**  
  - 실험 로그(날짜, 설정 요약, 메트릭 요약)를 디렉터리 규칙에 맞게 기록.  
  - 예: `YYYYMMDD_<실험명>_log.md` 또는 공용 로그 파일에 append.

생성하지 않아도 되는 것: model_specs, metrics_definition, system 프롬프트는 실험으로 인해 새로 만들지 않고, 기존 SSOT를 참조만 한다.

## 5.2 모델 배포 전 체크리스트

- [ ] **04_model/model_specs**에 반영할 스펙 변경이 있으면 해당 문서를 먼저 수정했는가?
- [ ] **04_model/model_versions**에 이번 배포 버전·적용된 model_specs 경로(또는 해시)·적용일을 추가했는가?
- [ ] 실험 결과(05_experiment/results)를 model_specs에 직접 붙여넣지 않고, “결론 → 스펙 문구”로만 반영했는가?
- [ ] 배포 스크립트/원테이크가 참조하는 SSOT 문서 경로가 단일하며, CE/Queue/JobDef 등 리소스 이름이 model_specs와 일치하는가?

## 5.3 프롬프트 변경 시 영향 범위

- **06_prompt/system** 변경 시:  
  - 변경 전 파일을 **06_prompt/archived_versions/**에 `YYYYMMDD_<이름>_v<N>.md` 형태로 복사.  
  - system 문서만 수정.  
  - 이 프롬프트를 참조하는 배치/자동화(예: 검증 스크립트, AI 워커) 목록을 system 문서 상단 또는 10_ops 체크리스트에 명시.  
  - 배포/재배포가 필요하면 10_ops 런북에 “프롬프트 변경 시 재배포 필요” 여부를 적어 둔다.

- **06_prompt/evaluation_prompts** 변경 시:  
  - 평가·벤치마크에만 영향. 운영 서빙에는 반영하지 않는다는 점을 문서에 명시.

## 5.4 ADR 작성 기준

- **11_adr**에 ADR을 작성하는 경우:  
  - 아키텍처/설계 결정(기술 선택, 리소스 명명, 파이프라인 구조, 데이터/평가 정책 등)이 팀 합의나 정책으로 확정될 때.  
  - 실험 단순 결과만으로는 ADR을 쓰지 않고, “그 결과를 반영한 결정”을 ADR로 남긴다.  
  - ADR 번호·제목·상태(제안/수락/폐기)·결정·이유·결과를 템플릿으로 유지한다.

---

# 부록: 신규 팀원 1시간 내 구조 이해를 위한 진입 경로

1. **docs/README.md**  
   - 상위 폴더 구조(00_strategy ~ 11_adr, 99_archive)와 “무엇을 어디에 두는가” 1페이지 요약.

2. **04_model/model_specs**  
   - 현재 프로덕션 모델(및 인프라) 스펙 단일 문서.  
   - **04_model/model_versions**에서 “현재 프로덕션” 행 한 개로 버전 확인.

3. **06_prompt/system**  
   - 운영용 AI/검증 프롬프트 목록과 용도.

4. **07_evaluation/metrics_definition**  
   - 평가 지표 정의 한 곳.

5. **10_ops**  
   - 배포·운영 런북·체크리스트.

6. **05_experiment**  
   - 실험은 hypothesis → experiment_logs → results 순서로만 남기며, 스펙 문서와 분리되어 있음을 안내.

이 문서 자체는 **실행 가능한 수준**으로 유지하며, 폴더 생성·이동·SSOT 통합은 이 매핑과 규칙에 따라 순차 적용하면 된다.
