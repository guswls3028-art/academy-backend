# Video Batch: setup 스크립트 vs API 서버 설정 일치 여부

**검사 대상:**  
- 인프라: `.\scripts\infra\batch_video_setup_full.ps1` (→ batch_video_setup.ps1 + batch_video_verify_and_register.ps1)  
- API: `apps/api/config/settings/base.py` + API 서버 `.env` (또는 기본값)

---

## 1. 비교 요약

| 항목 | batch_video_setup_full.ps1 쪽 | API 서버 (base.py / .env) | 일치 |
|------|-------------------------------|----------------------------|------|
| **Job Queue 이름** | `academy-video-batch-queue` (batch_video_setup.ps1 L17, video_job_queue.json) | `os.getenv("VIDEO_BATCH_JOB_QUEUE", "academy-video-batch-queue")` / .env.example 동일 | ✅ |
| **Job Definition 이름** | `academy-video-batch-jobdef` (batch_video_setup.ps1 L18, verify L14) | `os.getenv("VIDEO_BATCH_JOB_DEFINITION", "academy-video-batch-jobdef")` / .env.example 동일 | ✅ |

API는 **Queue 이름**과 **Job Definition 이름**만 사용하므로, 위 두 값이 같으면 설정은 일치한다.

---

## 2. 상세 출처

### 2.1 인프라 (setup 스크립트)

- **batch_video_setup.ps1** (full에서 호출)
  - `$JobQueueName = "academy-video-batch-queue"` (L17)
  - `$JobDefName = "academy-video-batch-jobdef"` (L18)
  - Job Queue JSON: `scripts/infra/batch/video_job_queue.json`  
    → `jobQueueName: academy-video-batch-queue`, `computeEnvironment: academy-video-batch-ce`
- **batch_video_verify_and_register.ps1** (full에서 호출)
  - `$JobDefName = "academy-video-batch-jobdef"` (L14)
  - 테스트 제출 시 큐: `$JobQueueName = "academy-video-batch-queue"` (L105)

### 2.2 API 서버

- **base.py** (L348–349)
  - `VIDEO_BATCH_JOB_QUEUE = os.getenv("VIDEO_BATCH_JOB_QUEUE", "academy-video-batch-queue")`
  - `VIDEO_BATCH_JOB_DEFINITION = os.getenv("VIDEO_BATCH_JOB_DEFINITION", "academy-video-batch-jobdef")`
- **batch_submit.py**
  - `queue_name = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-video-batch-queue")`
  - `job_def_name = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "academy-video-batch-jobdef")`
- **.env.example** (L66–67)
  - `VIDEO_BATCH_JOB_QUEUE=academy-video-batch-queue`
  - `VIDEO_BATCH_JOB_DEFINITION=academy-video-batch-jobdef`

---

## 3. API가 사용하지 않는 인프라 값 (참고)

다음은 setup이 만들지만, API 설정에는 **넣지 않아도 되는** 값이다.

| 항목 | setup 쪽 값 | 비고 |
|------|-------------|------|
| Compute Environment | `academy-video-batch-ce` (batch_video_setup.ps1 / video_job_queue.json) | 큐가 CE를 참조. API는 큐 이름만 지정. |
| CE v3/v4 | batch_update_ce_ami.ps1 등에서 `academy-video-batch-ce-v3`, `-v4` 사용 가능 | 큐를 새 CE로 바꿔도 **큐 이름**은 그대로 `academy-video-batch-queue`이면 API 변경 불필요. |
| Log group | `/aws/batch/academy-video-worker` | 워커 로그용. API env 아님. |
| ECR URI | `809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest` | Job Definition에만 사용. API env 아님. |
| IAM 역할 | academy-video-batch-job-role, academy-batch-ecs-task-execution-role | Job Def/실행 시 사용. API env 아님. |

---

## 4. 결론

- **`.\scripts\infra\batch_video_setup_full.ps1`로 만든 인프라**와 **API 서버 설정(또는 .env / base 기본값)** 은 **일치한다.**
- API 서버에는 다음만 맞으면 된다.
  - `VIDEO_BATCH_JOB_QUEUE=academy-video-batch-queue` (또는 미설정 시 기본값)
  - `VIDEO_BATCH_JOB_DEFINITION=academy-video-batch-jobdef` (또는 미설정 시 기본값)
- `.env`에 위 두 변수를 넣지 않아도 base.py 기본값이 위와 같으므로, **setup 한 번 돌린 뒤 API만 배포해도** 동일 계정/리전이면 그대로 사용 가능하다.
- 단, 다른 계정/리전에서 다른 이름으로 큐·Job Def를 만든 경우에는, 그 이름에 맞게 API 서버 `.env`에 `VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION`을 설정해야 한다.
