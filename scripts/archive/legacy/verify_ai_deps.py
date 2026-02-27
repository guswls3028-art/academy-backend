#!/usr/bin/env python3
"""
AI Worker 의존성 격리 검증

CPU 이미지: torch CUDA 미포함, onnxruntime (CPU)
GPU 이미지: torch CUDA 포함, onnxruntime-gpu

사용법:
  python scripts/verify_ai_deps.py --mode cpu   # CPU 환경 검증
  python scripts/verify_ai_deps.py --mode gpu   # GPU 환경 검증

CI/Docker 빌드 후 각 이미지 내부에서 실행하여 의존성 오염 여부 확인.
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def _pip_has(package: str) -> bool:
    """pip list에 정확한 패키지명이 있는지 확인 (onnxruntime vs onnxruntime-gpu 구분)"""
    r = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=freeze"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return False
    pkg = package.lower()
    for line in (r.stdout or "").strip().splitlines():
        part = line.split("==")[0].strip().lower()
        if part == pkg:
            return True
    return False


def verify_cpu() -> bool:
    """CPU 환경: CUDA 미포함, onnxruntime(CPU) 사용"""
    errors = []
    # torch CUDA 없어야 함
    try:
        import torch

        if torch.cuda.is_available():
            errors.append("torch.cuda.is_available() is True (CPU env should not have CUDA)")
        if getattr(torch.version, "cuda", None):
            errors.append("torch.version.cuda is set (CPU env should use torch CPU wheel)")
    except ImportError as e:
        errors.append(f"torch import failed: {e}")
        return False

    # onnxruntime (CPU) 있어야 함
    try:
        import onnxruntime as ort

        # onnxruntime-gpu가 로드되면 providers에 CUDAExecutionProvider 포함
        providers = getattr(ort, "get_available_providers", lambda: [])()
        if "CUDAExecutionProvider" in providers:
            errors.append("onnxruntime has CUDA provider (should use CPU-only onnxruntime)")
    except ImportError as e:
        errors.append(f"onnxruntime import failed: {e}")

    # onnxruntime-gpu 없어야 함 (의존성 오염)
    if _pip_has("onnxruntime-gpu"):
        errors.append("onnxruntime-gpu installed (CPU env should not have it)")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False
    print("  OK: CPU deps verified (no CUDA, onnxruntime CPU)")
    return True


def verify_gpu() -> bool:
    """GPU 환경: torch CUDA 포함, onnxruntime-gpu"""
    errors = []
    # torch CUDA 있어야 함
    try:
        import torch

        if not getattr(torch.version, "cuda", None):
            errors.append("torch.version.cuda is None (GPU env should have torch+cu121)")
        # cuda.is_available()은 호스트에 GPU가 없으면 False - 런타임 환경 의존
        # 따라서 version.cuda만 체크
    except ImportError as e:
        errors.append(f"torch import failed: {e}")
        return False

    # onnxruntime-gpu 있어야 함
    if not _pip_has("onnxruntime-gpu"):
        errors.append("onnxruntime-gpu not installed (GPU env should have it)")

    try:
        import onnxruntime as ort

        # GPU env에서는 CUDA provider 사용 가능해야 함 (드라이버 있으면)
        providers = getattr(ort, "get_available_providers", lambda: [])()
        # GPU 머신이 아니면 CUDAExecutionProvider 없을 수 있음 - 패스
    except ImportError:
        errors.append("onnxruntime (gpu) import failed")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False
    print("  OK: GPU deps verified (torch+cu, onnxruntime-gpu)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Worker 의존성 격리 검증")
    parser.add_argument("--mode", choices=["cpu", "gpu"], required=True)
    args = parser.parse_args()
    ok = verify_cpu() if args.mode == "cpu" else verify_gpu()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
