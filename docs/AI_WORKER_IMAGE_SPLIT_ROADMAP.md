# AI Worker 이미지 분리 로드맵

## 현재 구조 (개발 단계 — 논리적 분리만)

```
academy-ai-worker (단일 이미지)
 ├─ sqs_main_cpu   → lite, basic 큐
 ├─ sqs_main_gpu   → premium 큐
 ├─ sqs_main       → 통합 진입점
 └─ 모든 AI 모듈 포함
     ├─ torch (CPU + CUDA wheel)
     ├─ onnxruntime
     ├─ opencv
     ├─ sentence-transformers
     ├─ google vision
     ├─ openai
     ├─ skimage
     ├─ ffmpeg
     └─ ...
```

**문제점:**
- CPU 인스턴스에서도 CUDA 패키지 포함 이미지 사용 → 구조적 낭비
- 이미지 빌드 시간 2~3배
- 이미지 크기 과대
- Cold start 지연
- ECR 스토리지/전송 비용 증가

**개발 단계에서의 장점:**
- 배포 단순
- 운영 단순
- 디버깅 용이
- Dockerfile 하나로 관리

---

## 목표 구조 (프로덕션 — 비용 최적화)

### 1️⃣ 이미지 분리

| 이미지 | Base | 포함 패키지 | 예상 크기 |
|--------|------|-------------|-----------|
| `academy-ai-worker-cpu` | python-slim | torch CPU only, no CUDA, no cuDNN, onnxruntime, opencv, sentence-transformers 등 | 700MB~1GB |
| `academy-ai-worker-gpu` | nvidia/cuda | torch+cu12, onnxruntime-gpu, cuDNN | 2~3GB |

### 2️⃣ SQS 분리 (유지)

이미 적용됨:
- `academy-ai-jobs-lite` → CPU
- `academy-ai-jobs-basic` → CPU
- `academy-ai-jobs-premium` → GPU

### 3️⃣ 빌드/배포 변경

```
docker/ai-worker-cpu/Dockerfile   # CPU 전용 (torch CPU, CUDA 제외)
docker/ai-worker-gpu/Dockerfile   # GPU 전용 (nvidia/cuda base)

requirements/worker-ai-cpu.txt    # torch, onnxruntime 등 CPU only
requirements/worker-ai-gpu.txt    # torch+cu12, onnxruntime-gpu
```

---

## 비용 차이 예측 (10K DAU 기준)

| 항목 | 단일 이미지 | 분리 전략 |
|------|-------------|-----------|
| CPU 워커 이미지 | 1.5~2GB | 700MB~1GB |
| GPU 워커 이미지 | 1.5~2GB (동일) | 2~3GB |
| 네트워크 전송 | 매번 전체 | CPU는 절반 이하 |
| Cold start | 느림 | CPU 단축 |
| ECR 스토리지 | 중복 | 역할별 최적화 |

---

## 마이그레이션 단계

| 단계 | 작업 | 비고 |
|------|------|------|
| 0 | 현재: 단일 이미지 | 개발/정비 단계 |
| 1 | `worker-ai-cpu.txt` 생성 (CUDA 제외) | torch CPU, onnxruntime |
| 2 | `worker-ai-gpu.txt` 생성 | torch+cu, onnxruntime-gpu |
| 3 | `docker/ai-worker-cpu/`, `docker/ai-worker-gpu/` 분리 | Dockerfile 각각 |
| 4 | docker-compose, build.ps1 수정 | ai-worker-cpu, ai-worker-gpu |
| 5 | ECS/EC2 태스크 정의 분리 | cpu 이미지 / gpu 이미지 |

---

## 참고

- [WORKER_ARCHITECTURE_FACT_REPORT.md](WORKER_ARCHITECTURE_FACT_REPORT.md)
- [DEPLOYMENT_MASTER_GUIDE.md](DEPLOYMENT_MASTER_GUIDE.md)
