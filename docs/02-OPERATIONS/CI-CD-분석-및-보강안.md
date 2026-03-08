# Academy 백엔드 CI/CD 구조 분석 및 보강안

**작성일:** 2026-03-09  
**기준:** 실제 코드·스크립트 (docs가 아닌 실행 코드)

---

## 1. 현재 CI/CD 실제 흐름

### 1.1 GitHub Actions (빌드·푸시)

| 항목 | 내용 |
|------|------|
| **파일** | `.github/workflows/v1-build-and-push-latest.yml` |
| **트리거** | `push` to `main`, `workflow_dispatch` |
| **역할** | 5개 이미지(academy-base, academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu) **linux/arm64** 빌드 후 ECR에 **`latest` 태그만** 푸시 |
| **태그 전략** | **latest 전용**. SHA/커밋 태그 없음. (`tags: ${{ env.ECR_REGISTRY }}/academy-api:latest`) |
| **이미지 반영 서버** | **없음**. 워크플로우에는 deploy.ps1 호출·instance refresh·SSM 배포 단계가 **전혀 없음**. |
| **부가 동작** | 푸시 후 `docs/00-SSOT/v1/reports/ci-build.latest.md`에 digest 기록 후 해당 파일만 커밋·푸시 |

### 1.2 ECR 푸시 태그 전략

- **사용 태그:** `latest` 만 사용 (params.yaml `ecr.useLatestTag: true`).
- **SHA/버전 태그:** 없음. 동일 `latest`를 덮어쓰므로 “지금 서버가 어떤 커밋 이미지인지”는 ECR 태그만으로는 구분 불가.
- **구분 방법:** `ci-build.latest.md`의 `academy-api | latest | sha256:xxx` digest로 “마지막 CI 빌드”와 비교 가능.

### 1.3 scripts/v1/deploy.ps1 역할

| 항목 | 내용 |
|------|------|
| **역할** | ECR에 **이미 올라가 있는** 이미지를 전제로, 인프라 Ensure + **API Launch Template 갱신 + API ASG instance refresh** 수행. |
| **빌드** | **하지 않음.** `-SkipBuild` 기본값 true, 주석/코드상 “GitHub Actions OIDC만 빌드”. |
| **API 이미지** | `Get-LatestApiImageUri` → `ecr.useLatestTag` 시 `.../academy-api:latest` 고정. |
| **Launch Template** | `Ensure-API-LaunchTemplate`: UserData에 `docker pull $ApiImageUri`(latest) + `docker run` 포함. **UserData 내용은 항상 동일 문자열**(이미지 URI가 같으므로). |
| **Instance refresh** | LT가 **drift**일 때만 `start-instance-refresh` 실행. Drift 판단은 `diff.ps1`에서 **API LT**에 대해 `expectedUserData`(params `api.userData`, 비어 있으면 `""`) vs **actual** UserData 비교. |
| **중요** | params에 `api.userData: ""` 이므로 expected가 빈 문자열. 실제 LT에는 긴 UserData base64가 있어 **매번 drift로 간주**되어, deploy.ps1 실행 시 API LT 새 버전 생성 + instance refresh가 **항상** 발생함. (단, **CI에서 deploy.ps1을 호출하지 않으므로** push만으로는 refresh가 일어나지 않음.) |

### 1.4 api-auto-deploy-remote.ps1 역할

| 항목 | 내용 |
|------|------|
| **역할** | API ASG 인스턴스에 대해 **SSM Send-Command**로 원격 제어. |
| **Actions** | Status(crontab 확인), Off(cron 제거), **On**(cron 등록), **Deploy**(한 번 실행). |
| **On** | 인스턴스에서 `git fetch origin main && git reset --hard origin/main` 후 `scripts/auto_deploy_cron_on.sh` 실행 → 2분마다 main 변경 시 `deploy_api_on_server.sh` 실행하는 cron 등록. |
| **Deploy** | 동일 repo 준비 후 `scripts/deploy_api_on_server.sh` **1회** 실행. |
| **자동 반영** | **On** 상태일 때만, main이 바뀐 뒤 **2분 이내**에 서버가 `deploy_api_on_server.sh`를 실행해 ECR pull + 재시작. **CI와 연동되지 않음** (main push는 CI가 하고, 서버의 cron이 주기적으로 main 변경을 감지). |

### 1.5 서버 UserData / deploy_api_on_server.sh

| 항목 | 내용 |
|------|------|
| **Launch Template UserData** | `scripts/v1/resources/api.ps1`의 `Get-ApiLaunchTemplateUserData`: 부팅 시 1회, ECR 로그인 → `docker pull $ApiImageUri`(latest) → SSM으로 `/opt/api.env` 생성 → `docker run -d ... academy-api`. **이미지 URI는 고정 `.../academy-api:latest`.** |
| **deploy_api_on_server.sh** | SSM → `/opt/api.env`, `docker pull` ECR_URI(기본 `.../academy-api:latest`), `docker stop/rm` 후 `docker run` (빌드 없음). 정석 배포와 동일 결과. |
| **반영 시점** | (1) **새 인스턴스 부팅 시** UserData 1회 (2) **원격 Deploy 또는 cron**으로 `deploy_api_on_server.sh` 실행 시. |

### 1.6 ASG instance refresh와 이미지 반영 관계

- **Instance refresh가 일어나면:** 새 인스턴스가 뜨고, UserData로 **그 시점의** `academy-api:latest`를 pull 해서 실행. 즉 **refresh 완료 시점의 ECR latest**가 반영됨.
- **Refresh가 안 일어나면:** 기존 인스턴스는 **한 번도 재시작하지 않는 한** 예전에 pull한 이미지로 계속 동작. **push = 반영**이 되려면 (1) CI 후 누군가 deploy.ps1을 실행하거나, (2) 원격 자동배포 On + main 변경 후 cron이 돌아야 함.

---

## 2. main push 시 API 코드가 ECR 이미지로 빌드/푸시되는지

- **예.**  
  - main에 push → `v1-build-and-push-latest.yml` 실행 → academy-api 포함 5개 이미지 빌드 후 `academy-api:latest` 등 ECR에 푸시.  
  - 따라서 **“main push → ECR에 최신 API 이미지 올라감”** 은 **이미 만족**.

---

## 3. ASG 기반 API 서버에 최신 이미지가 자동 반영되는지

- **아니오.**  
  - CI 워크플로우에는 **deploy.ps1 호출, instance refresh 트리거, SSM Deploy 호출**이 **전혀 없음.**  
  - 따라서 **push만으로는** 기동 중인 API 인스턴스가 새 이미지를 pull하거나 재시작하지 않음.

---

## 4. 끊기는 지점 (사실 기반)

| 단계 | 상태 | 비고 |
|------|------|------|
| 1. main push | ✅ | 개발자/GitHub |
| 2. GitHub Actions 빌드·ECR 푸시 | ✅ | `v1-build-and-push-latest.yml` |
| 3. **“배포” 트리거** | ❌ **끊김** | CI에 “ECR 푸시 후 배포” 단계 없음. deploy.ps1 호출 없음, refresh/SSM 호출 없음. |
| 4. API 인스턴스가 새 이미지 사용 | ⚠️ 수동/조건부 | deploy.ps1 수동 실행 시 instance refresh로 반영, 또는 원격 자동배포 On + main 변경 시 2분마다 cron으로 반영. |

**정리:** 끊기는 지점은 **“CI에서 ECR 푸시 이후, API 서버에 최신 이미지를 쓰게 만드는 단계가 없다는 것”** 이다.

---

## 5. 최신 커밋이 실제 서버 컨테이너 이미지에 반영되었는지 검증하는 방법

1. **CI digest vs 런타임 digest**  
   - `docs/00-SSOT/v1/reports/ci-build.latest.md`에서 `academy-api | latest | sha256:xxx` 확인.  
   - `scripts/v1/resources/api.ps1`의 `Invoke-CollectRuntimeImagesReport`가 API ASG 인스턴스에서 `docker inspect academy-api --format '{{json .RepoDigests}}'`로 수집한 digest와 비교.  
   - deploy.ps1 실행 후 `docs/00-SSOT/v1/reports/runtime-images.latest.md`에 “CI vs Runtime **MISMATCH**” 여부가 찍힘.
2. **수동 검증**  
   - API 인스턴스에 SSM 접속 후  
     `docker inspect academy-api --format '{{.RepoDigests}}'`  
   - ECR에서 현재 latest digest:  
     `aws ecr describe-images --repository-name academy-api --image-ids imageTag=latest --region ap-northeast-2 --query 'imageDetails[0].imageDigest' --output text`  
   - 두 값이 같으면 “지금 서버 = ECR latest”.

---

## 6. 즉시 수정안 — “push = 실제 서버 반영” 보강

**목표:** main push → CI 빌드·푸시 후 **자동으로** 기동 중인 API가 최신 이미지를 pull해 재시작하도록 한다.

### 6.1 방안 A: CI에서 배포 스크립트 호출 (권장)

- **할 일:** `v1-build-and-push-latest.yml`에 **빌드·푸시 job 완료 후** “API 배포” job 추가.
- **배포 방식:**  
  - **옵션 1)** AWS CLI OIDC로 `autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg` 호출.  
    - 장점: 코드 변경 최소, 기존 deploy.ps1 로직 그대로 활용(이미 LT는 latest 기준).  
    - 단점: refresh 완료까지 수 분 소요, 그동안 기존 인스턴스는 예전 이미지.  
  - **옵션 2)** OIDC로 SSM `SendCommand` 권한을 가진 역할 사용해, API ASG 인스턴스에 `deploy_api_on_server.sh` 실행 (현재 `api-auto-deploy-remote.ps1`이 하는 것과 동일).  
    - 장점: 기존 인스턴스가 곧바로 pull + 재시작, refresh보다 빠름.  
    - 단점: GitHub Actions에서 인스턴스 ID 조회 + SSM SendCommand 호출 필요.
- **공통:** GitHub OIDC용 역할에 `autoscaling:StartInstanceRefresh` 또는 `ssm:SendCommand` + `ec2:DescribeInstances` 등 필요한 권한 추가.

### 6.2 방안 B: CI에서 deploy.ps1 실행 (전체 인프라 갱신)

- 워크플로우에 job 추가: `pwsh scripts/v1/deploy.ps1 -AwsProfile default` (또는 OIDC로 AWS 자격 증명 설정 후 동일 실행).  
- 장점: LT·ASG·기타 인프라까지 한 번에 맞춤.  
- 단점: 실행 시간 길고, 인프라 변경이 항상 일어남. “이미지만 반영”이 목적이면 과할 수 있음.

### 6.3 방안 C: 원격 자동배포 On 유지 + main만 올리기

- **현재 구조 그대로:** 원격 자동배포를 **On** 해 두면, main이 바뀔 때(CI가 빌드·푸시한 뒤 보통 ci-build.latest.md 커밋으로 한 번 더 push) 2분 이내에 cron이 `deploy_api_on_server.sh`를 실행해 **이미지 반영**됨.  
- **끊기는 지점 보강:** “push = 반영”을 **CI만으로** 보장하려면 A 또는 B가 필요. C는 “수동으로 On 해 두고 main에 올리면, 2분 안에 반영된다” 수준의 보강.

---

## 7. 즉시 수정 제안 (방안 A – instance refresh)

- **변경 파일:** `.github/workflows/v1-build-and-push-latest.yml`  
- **추가 내용:**  
  - build-and-push job 다음에 `deploy-api` job 추가.  
  - 같은 OIDC 역할(또는 배포용 역할)로 AWS 자격 증명 설정 후,  
    `aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg --region ap-northeast-2` 실행.  
- **필요 권한:** 해당 역할에 `autoscaling:StartInstanceRefresh`, `autoscaling:DescribeAutoScalingGroups` 등 (이미 deploy.ps1을 돌릴 수 있는 역할이면 보통 있음).

이렇게 하면 **main push → CI 빌드·푸시 → API ASG instance refresh**까지 자동으로 이어져, “push = 실제 서버 반영”이 성립한다.

**반영 사항 (2026-03-09):** `.github/workflows/v1-build-and-push-latest.yml`에 `deploy-api-refresh` job을 추가함. 빌드·푸시 성공 후 같은 OIDC 역할(`AWS_ROLE_ARN_FOR_ECR_BUILD`)로 `aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg`를 실행한다. **해당 역할에 `autoscaling:StartInstanceRefresh`, `autoscaling:DescribeAutoScalingGroups` 권한이 없으면 이 job이 실패하므로**, IAM에서 해당 권한을 추가해야 한다.

---

## 8. 정석 개선안 (중장기)

1. **태그 전략**  
   - CI에서 `academy-api:latest` 외에 `academy-api:sha-${SHORT_SHA}` 같은 태그도 푸시.  
   - Launch Template UserData 또는 deploy_api_on_server.sh에서 **특정 태그**(예: SSM 파라미터에 저장한 “배포할 이미지 태그”)를 참조하게 하면, 롤백·추적이 쉬워짐.
2. **배포 단일 진입점**  
   - “이미지 반영만” 할 때는 SSM으로 `deploy_api_on_server.sh` 실행.  
   - “인프라 변경 포함 전체 배포”일 때만 deploy.ps1 실행.  
   - CI는 “이미지 반영” job만 두고, 전체 배포는 수동 또는 별도 워크플로로 유지.
3. **검증 자동화**  
   - CI 마지막에 “API health 200 + (선택) 런타임 digest == ci-build.latest.md” 검사 단계를 두면, 반영 실패를 빌드 결과에서 바로 확인 가능.

---

## 9. 검증 방법 요약

| 목적 | 방법 |
|------|------|
| ECR에 latest가 올라갔는지 | `aws ecr describe-images --repository-name academy-api --image-ids imageTag=latest --region ap-northeast-2` |
| CI 빌드 digest | `docs/00-SSOT/v1/reports/ci-build.latest.md`의 academy-api 행 |
| 서버가 쓰는 이미지 digest | SSM 또는 배포 스크립트로 `docker inspect academy-api --format '{{.RepoDigests}}'` |
| push = 반영 여부 | 위 두 digest 일치 + `/healthz` 200 및 앱 동작 확인 |
