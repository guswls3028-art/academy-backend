# AI Worker 이미지 분리 빌드 리포트

## 개요

AI Worker를 CPU 전용과 GPU 전용 이미지로 분리하여 비용과 속도를 최적화합니다.

---

## 이미지별 예상 용량

| 이미지 | 베이스 | 예상 크기 | 비고 |
|--------|--------|-----------|------|
| **academy-ai-worker-cpu** | python:3.11-slim | **~700MB ~ 1.2GB** | torch CPU, onnxruntime CPU |
| **academy-ai-worker-gpu** | nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04 | **~2.5GB ~ 3.5GB** | torch+cu121, onnxruntime-gpu |

### 참고

- CPU 이미지: CUDA 미포함 → 스케일아웃에 유리, 비용 절감
- GPU 이미지: CUDA 런타임 포함 → 추론 속도 최적화, 무거운 모델 전용

---

## 빌드 시간 예상

| 이미지 | 예상 빌드 시간 | 비고 |
|--------|----------------|------|
| **academy-ai-worker-cpu** | **3 ~ 8분** | slim 베이스, torch CPU wheel |
| **academy-ai-worker-gpu** | **8 ~ 15분** | CUDA 베이스, Python 3.11 설치, torch+cu |

※ 네트워크·캐시 상태에 따라 달라질 수 있음

---

## build.ps1 명령어

```powershell
# AI Worker CPU만 빌드
.\docker\build.ps1 -AiWorkerCpu

# AI Worker GPU만 빌드
.\docker\build.ps1 -AiWorkerGpu

# AI Worker CPU + GPU 둘 다 빌드
.\docker\build.ps1 -AiWorkerBoth

# 전체 이미지 빌드 (기존 + CPU/GPU 분리 이미지 포함)
.\docker\build.ps1
```

---

## 의존성 검증

빌드 후 의존성 오염 여부 확인:

```powershell
# AI Worker 이미지 빌드 완료 후
python scripts/final_sanity_check.py --check-ai-isolation
```

- `academy-ai-worker-cpu`: torch CUDA 없음, onnxruntime (CPU)
- `academy-ai-worker-gpu`: torch+cu121, onnxruntime-gpu

---

## 런타임 분기 (WORKER_TYPE)

| 환경 변수 | SQS 큐 | 용도 |
|-----------|--------|------|
| `WORKER_TYPE=CPU` | lite, basic | 경량 OCR, embedding 등 |
| `WORKER_TYPE=GPU` | premium | OMR 채점, 세그멘테이션 등 |

엔트리포인트:

- CPU: `python -m apps.worker.ai_worker.sqs_main_cpu`
- GPU: `python -m apps.worker.ai_worker.sqs_main_gpu`

---

## 요약

| 항목 | 내용 |
|------|------|
| Requirements | worker-ai-common / worker-ai-cpu / worker-ai-gpu 분리 |
| Dockerfile | ai-worker-cpu (slim), ai-worker-gpu (cuda) |
| Adapter | WORKER_TYPE → SQS tier 분기 |
| 검증 | `verify_ai_deps.py`, `final_sanity_check.py --check-ai-isolation` |
