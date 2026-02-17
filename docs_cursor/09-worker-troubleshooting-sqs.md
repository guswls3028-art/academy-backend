# 워커 작업(영상 인코딩, 엑셀 수강등록) 안 될 때 — 원인 규명

인코딩(워커)과 엑셀 수강등록이 안 되는 경우, **설정(AWS/SQS)** 문제인지 **코드** 문제인지 구분하려면 아래 순서로 확인하세요.

---

## 1단계: SQS 연결 진단 (필수)

**API 서버와 동일한 환경**에서 아래 스크립트를 실행한 뒤, **전체 출력 결과**를 확인하세요.

```bash
cd C:\academy
set DJANGO_SETTINGS_MODULE=apps.api.config.settings.base
python scripts/check_sqs_worker_connectivity.py
```

- **Linux/Mac**: `export DJANGO_SETTINGS_MODULE=apps.api.config.settings.base` 후 실행  
- **API가 Docker로 돌아가면**: API 컨테이너 안에서 실행  
  ```bash
  docker exec -it <api_container_name> python scripts/check_sqs_worker_connectivity.py
  ```
- **.env 사용 시**: 해당 터미널에서 `.env`를 로드한 뒤 위처럼 실행 (또는 `python -c "from dotenv import load_dotenv; load_dotenv(); ..."` 등으로 로드 후 스크립트 실행)

### 결과 해석

| 스크립트 출력 | 의미 | 조치 |
|---------------|------|------|
| `[1] Video 큐: ... get_queue_url: OK`, `[2] AI(Basic) 큐: ... get_queue_url: OK` | SQS 연결 정상 | 2단계(워커 실행/로그) 확인 |
| `FAIL: 큐 접근 불가 (자격 증명/권한 문제)` 또는 `InvalidClientTokenId` / `SignatureDoesNotMatch` | AWS 자격 증명 없음/잘못됨/만료 | API·워커 서버에 올바른 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`(또는 `AWS_DEFAULT_REGION`) 설정. IAM 사용 시 해당 역할에 SQS `GetQueueUrl`, `SendMessage`, `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility` 권한 필요 |
| `FAIL: 큐가 존재하지 않습니다` 또는 `QueueDoesNotExist` / `NonExistentQueue` | 해당 리전에 큐 없음 | 같은 리전(예: ap-northeast-2)에서 `python scripts/create_sqs_resources.py`, `python scripts/create_ai_sqs_resources.py` 실행해 큐 생성 |
| `FAIL: AWS 자격 증명 오류 또는 SQS 권한 없음` | 권한 부족 | IAM 정책에 SQS 위 권한 추가 |

**정리**:  
- **설정 문제** → 위 표의 조치 후 API/워커 재기동  
- **스크립트는 전부 OK인데도 작업이 안 됨** → 2단계(워커 프로세스·로그) 확인

---

## 2단계: 워커 실행 여부 및 로그

SQS 진단이 모두 OK이면, 워커가 실제로 떠 있는지와 에러 로그를 봅니다.

- **Video Worker**  
  - 실행 여부: `docker ps \| findstr video-worker` (또는 `docker ps -a`)  
  - 로그: `docker logs <video_worker_container>`  
  - 기대: `Video Worker (SQS) started`, `SQS_MESSAGE_RECEIVED`, `SQS_JOB_COMPLETED` 등  
  - 에러 예: `SQS unavailable`, `Queue URL unavailable`, `Error enqueuing`(이건 API 로그)

- **AI Worker (엑셀 수강등록)**  
  - 실행 여부: `docker ps \| findstr ai-worker`  
  - 로그: `docker logs <ai_worker_container>`  
  - 기대: `EXCEL_PARSING processed_by=worker` 등

---

## 3단계: API 쪽 로그

- **영상**  
  - 업로드 완료 직후: `Video job enqueued: video_id=...` → SQS 전송 성공  
  - `Failed to enqueue video job` 또는 503 응답 → SQS 전송 실패(1단계 재확인)  
- **엑셀 수강등록**  
  - `EXCEL_PARSING dispatch ... job_id=...` → Job 생성 및 SQS 발행 시도  
  - 그 다음 에러 로그가 있으면 발행 실패(역시 SQS/자격 증명 확인)

---

## 요약

1. **반드시 먼저**: `scripts/check_sqs_worker_connectivity.py`를 **API와 동일한 환경**에서 실행하고, 출력 전체를 확인해 위 표대로 원인 특정.  
2. **스크립트가 모두 OK**이면 워커 프로세스·로그와 API 로그로 진행.  
3. **코드 버그 가능성**은 SQS 진단이 OK이고, 워커도 떠 있는데 특정 job만 반복 실패할 때 함께 확인하면 됩니다.
