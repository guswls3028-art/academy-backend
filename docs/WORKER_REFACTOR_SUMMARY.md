# Worker 이상향 리팩토링 요약

## 0) 사실 확인 리포트

→ `docs/WORKER_ARCHITECTURE_FACT_REPORT.md` 참조

---

## 1) 변경 사항

### A. apps.api 의존 제거

| 변경 | 내용 |
|------|------|
| `apps/core/models/base.py` | TimestampModel, BaseModel 정의 (신규) |
| `apps/api/common/models.py` | core.models.base에서 re-export |
| `apps/support/video/models.py` | `from apps.core.models.base import TimestampModel` |
| `apps/domains/ai/models.py` | `from apps.core.models.base import BaseModel` |

**결과**: Worker import 체인에서 `apps.api` 제거 (Video, AIJobModel 경로)

### B. Requirements 분리

| 파일 | 변경 |
|------|------|
| **common.txt** | Django, psycopg2, redis, django-extensions 추가 (API/Worker 공통) |
| **api.txt** | `-r ./common.txt` + djangorestframework, gunicorn 등 API 전용만 |
| **worker-video.txt** | `-r ./common.txt` + ffmpeg, pillow, opencv (api.txt 제거) |
| **worker-ai.txt** | `-r ./common.txt` + numpy, torch, pytesseract 등 (api.txt 제거) |
| **worker-messaging.txt** | `-r ./common.txt` + solapi (api.txt 제거) |

### C. Dockerfile

- Worker 이미지: `api.txt` COPY 제거, `worker-*.txt` + `common.txt` 만 사용
- `-r ./common.txt` 상대경로 통일

### D. Forbidden Import 테스트

- `scripts/check_worker_forbidden_imports.py` 추가
- `scripts/check_worker_deps.ps1` 추가 (이미지 내 API 패키지 검사)

---

## 2) 검증 절차

```powershell
# 1. Forbidden import 검사
python scripts/check_worker_forbidden_imports.py

# 2. check_no_celery
python scripts/check_workers.py

# 3. Docker 빌드
.\docker\build.ps1

# 4. Worker import 검증
python scripts/check_workers.py --docker

# 5. Worker 이미지 API 패키지 검사
.\scripts\check_worker_deps.ps1
```

---

## 3) Import Graph 요약

**Before**: Worker → support.video.models.Video → **apps.api.common.models**

**After**: Worker → support.video.models.Video → **apps.core.models.base**

Worker가 `apps.api`를 import하지 않음.
