# Full Rebuild SSOT v3 — 인프라 정렬 Audit 리포트

**목적:** 합격 시 즉시 prod deploy 가능 여부 판정. **코드 수정 없음, Audit만 수행.**

**기준 레포:** 현재 레포 (scripts_v3 + scripts/infra/batch 등)

---

## [1] scripts_v3 구조 점검

### 1.1 필수 파일 존재 및 로드 가능 여부

| 파일 | 존재 | 비고 |
|------|------|------|
| deploy.ps1 | ✅ | scripts_v3/deploy.ps1 |
| core/logging.ps1 | ✅ | |
| core/aws-wrapper.ps1 | ✅ | |
| core/wait.ps1 | ✅ | |
| core/preflight.ps1 | ✅ | |
| core/evidence.ps1 | ✅ | |
| resources/batch.ps1 | ✅ | |
| resources/jobdef.ps1 | ✅ | |
| resources/eventbridge.ps1 | ✅ | |
| resources/iam.ps1 | ✅ | |
| netprobe/batch.ps1 | ✅ | |

deploy.ps1은 위 파일들을 `Join-Path $ScriptRoot "core\..."`, `"resources\..."`, `"netprobe\..."` 로만 dot-source 로드.  
**정상 로드 가능.**

### 1.2 scripts/infra/*.ps1 실행 호출 검사

- **검사 패턴:** `scripts[/\\]infra[/\\][^"']*\.ps1`, `Invoke-Expression`, `&\s*\.\\`, `Start-Process.*infra`, `pwsh.*scripts/infra`
- **결과:** **0건** (매치 없음).
- **판정:** **PASS** — scripts_v3 내에서 scripts/infra/*.ps1 실행 호출 없음.

---

## [2] Full Rebuild 경로 점검

| 조건 | 코드 위치 | 판정 |
|------|-----------|------|
| CE 없으면 create 경로 존재 | batch.ps1: Video CE L76–80 (describe empty → New-VideoCE, Wait-CEValidEnabled), Ops CE L119–124 동일 | **PASS** |
| CE INVALID면 delete → Wait-CEDeleted → create → Wait-CEValidEnabled | batch.ps1: Video L86–102, Ops L129–145. delete-compute-environment 후 Wait-CEDeleted, New-*CE, Wait-CEValidEnabled | **PASS** |
| Queue 없으면 create 경로 존재 | batch.ps1: Video Queue L161–164 (describe empty → New-VideoQueue), Ops Queue L185–188 동일 | **PASS** |
| EventBridge rule 없으면 put-rule + put-targets | eventbridge.ps1: ruleExists false 시 put-rule (L43–46, L64–66), put-targets 항상 실행 (L52–53, L72–73) | **PASS** |
| JobDef drift 기반 revision | jobdef.ps1: Test-JobDefDrift, describe ACTIVE → Sort revision Desc → First 1, drift 시에만 Register-JobDefFromJson (L79–91) | **PASS** |
| Netprobe 실패 시 throw | netprobe/batch.ps1: FAILED 시 throw (L29–30), timeout 시 throw (L35), RUNNABLE 정체 시 throw (L22–23), submit 실패 시 throw (L11) | **PASS** |

**전체:** **PASS**

---

## [3] Ops CE 설계 정렬 점검

**파일:** `scripts/infra/batch/ops_compute_env.json`

| 항목 | 조건 | 실제 값 | 판정 |
|------|------|---------|------|
| instanceTypes 단일 | 1개만 지정 | `["c6g.large"]` | **PASS** |
| default_arm64 제거됨 | default_arm64 미사용 | 사용 안 함 (c6g.large만 사용) | **PASS** |
| maxvCpus <= 2 | 2 이하 | `maxvCpus: 2` | **PASS** |
| minvCpus = 0 | 0 | `minvCpus: 0` | **PASS** |
| ECS_AL2023 | ec2Configuration.imageType | `"ec2Configuration":[{"imageType":"ECS_AL2023"}]` | **PASS** |

**전체:** **PASS**

---

## [4] JSON Template 의존성 점검

| 검사 항목 | 결과 |
|-----------|------|
| scripts_v3가 scripts/infra/batch/*.json 을 읽기 전용으로만 사용 | batch.ps1, jobdef.ps1: Join-Path → ReadAllText / Get-Content / Test-Path 만 사용. 실행 호출 없음. **PASS** |
| register/create 시 file:// 로만 사용 | batch.ps1: create-compute-environment, create-job-queue에 `file://$($tmp -replace '\\','/')`. jobdef.ps1: register-job-definition에 `file://$($tmp -replace '\\','/')`. **PASS** |
| scripts/infra/*.ps1 직접 실행 없음 | [1]에서 grep 0건 확인. **PASS** |

**전체:** **PASS**

---

## [5] EventBridge 안전성 점검

| 항목 | 코드 위치 | 판정 |
|------|-----------|------|
| describe-rule이 try/catch로 감싸져 있음 | eventbridge.ps1 L36–42 (reconcile), L56–62 (scan_stuck). try { describe-rule; ruleExists = ($null -ne $rule) } catch { ruleExists = $false } | **PASS** |
| Queue 존재 체크 후 put-targets | eventbridge.ps1 L10–13: describe-job-queues로 Ops Queue 확인, 없으면 throw. 이후 put-targets만 수행. | **PASS** |

**전체:** **PASS**

---

## [6] 최종 판정

### 1) 구조 합격 여부

**PASS**

- 필수 scripts_v3 파일 존재 및 deploy.ps1 로드 경로 정상.
- scripts/infra/*.ps1 실행 호출 0건.
- Full Rebuild 경로(CE create/recreate, Queue create, EventBridge put-rule+put-targets, JobDef drift, Netprobe throw) 모두 코드상 충족.
- Ops CE JSON 설계 조건 충족.
- JSON 읽기 전용·file:// 사용·EventBridge try/catch·Queue 존재 후 put-targets 충족.

### 2) 운영 난이도 등급

**Low**

- 단일 진입점(deploy.ps1), JSON 템플릿 유지, Create/Recreate/Drift 경로 명확.
- 수동 bootstrap 불필요, 레거시 스크립트 실행 차단.

### 3) 즉시 prod deploy 가능 여부

**YES**

- 위 구조·경로·의존성·EventBridge·Ops CE 조건을 모두 만족하므로, 현재 레포 기준으로 **즉시 prod deploy 가능**으로 판정.

---

## 문제 발견 시 수정 가이드 (참고용, 본 Audit에서는 미발생)

본 Audit에서는 **FAIL 항목 없음**. 아래는 향후 점검에서 이슈가 있을 때만 참고.

| 문제 | 수정이 필요한 파일 | 수정 위치 | 수정 예시 (10줄 이하) |
|------|--------------------|-----------|------------------------|
| scripts/infra/*.ps1 실행 호출 발견 | deploy.ps1 또는 호출한 .ps1 | 해당 라인 | 해당 라인 삭제 또는 주석. deploy는 scripts_v3만 dot-source. |
| CE create 경로 누락 | resources/batch.ps1 | Ensure-VideoCE / Ensure-OpsCE | describe 반환 empty일 때 New-VideoCE/New-OpsCE 호출 후 Wait-CEValidEnabled 호출. |
| describe-rule 미감싸기 | resources/eventbridge.ps1 | describe-rule 호출부 | try { $r = Invoke-AwsJson ...; $ruleExists = ($null -ne $r) } catch { $ruleExists = $false }; if (-not $ruleExists) { put-rule } |
| Netprobe 실패 시 throw 미적용 | netprobe/batch.ps1 | status 분기 | if ($status -eq "FAILED") { throw "Netprobe FAILED: ..." }; 루프 후 throw "Netprobe timeout..." |

---

**Audit 완료. 코드 수정 없음.**
