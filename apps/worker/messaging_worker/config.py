# PATH: apps/worker/messaging_worker/config.py
"""메시지 발송 워커 설정 — 환경변수만 사용 (Django 불필요)"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env: {name}")
    return v


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    SOLAPI_API_KEY: str
    SOLAPI_API_SECRET: str
    SOLAPI_SENDER: str
    MESSAGING_SQS_QUEUE_NAME: str
    AWS_REGION: str
    SQS_WAIT_TIME_SECONDS: int
    # 알림톡 (카카오) — 템플릿 ENV로 관리, 코드 수정 없이 교체 가능
    SOLAPI_KAKAO_PF_ID: str
    SOLAPI_KAKAO_TEMPLATE_ID: str


def load_config() -> Config:
    try:
        return Config(
            SOLAPI_API_KEY=_require("SOLAPI_API_KEY"),
            SOLAPI_API_SECRET=_require("SOLAPI_API_SECRET"),
            SOLAPI_SENDER=_require("SOLAPI_SENDER"),
            MESSAGING_SQS_QUEUE_NAME=os.environ.get("MESSAGING_SQS_QUEUE_NAME", "academy-messaging-jobs"),
            AWS_REGION=os.environ.get("AWS_REGION", "ap-northeast-2"),
            SQS_WAIT_TIME_SECONDS=int(os.environ.get("MESSAGING_SQS_WAIT_SECONDS", "20")),
            # 알림톡: 미설정이면 SMS만 사용 (빈 문자열 허용)
            SOLAPI_KAKAO_PF_ID=os.environ.get("SOLAPI_KAKAO_PF_ID", "").strip(),
            SOLAPI_KAKAO_TEMPLATE_ID=os.environ.get("SOLAPI_KAKAO_TEMPLATE_ID", "").strip(),
        )
    except Exception as e:
        import logging
        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).critical("config error: %s", e)
        sys.exit(1)
