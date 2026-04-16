# apps/worker/ai/ocr/google.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

# google cloud vision
from google.cloud import vision  # type: ignore


@dataclass
class OCRResult:
    text: str
    confidence: Optional[float] = None
    raw: Optional[Any] = None


_cached_client: Optional[vision.ImageAnnotatorClient] = None


def _get_vision_client() -> vision.ImageAnnotatorClient:
    """
    Google Vision 클라이언트 생성.
    1. GOOGLE_APPLICATION_CREDENTIALS (파일 경로) — 기본
    2. GOOGLE_CREDENTIALS_JSON (JSON 문자열) — SSM env 주입용
    3. Default credentials (GCE 등)
    """
    global _cached_client
    if _cached_client is not None:
        return _cached_client

    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if creds_json:
        from google.oauth2 import service_account
        info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(info)
        _cached_client = vision.ImageAnnotatorClient(credentials=credentials)
    else:
        _cached_client = vision.ImageAnnotatorClient()

    return _cached_client


def google_ocr(image_path: str) -> OCRResult:
    """
    Worker에서 실행되는 Google OCR
    - GOOGLE_CREDENTIALS_JSON (JSON 문자열) 또는
    - GOOGLE_APPLICATION_CREDENTIALS (파일 경로) 사용
    """
    client = _get_vision_client()

    with open(image_path, "rb") as f:
        content = f.read()

    image = vision.Image(content=content)
    response = client.text_detection(image=image)

    if getattr(response, "error", None) and response.error.message:
        return OCRResult(text="", confidence=None, raw={"error": response.error.message})

    annotations = getattr(response, "text_annotations", None) or []
    if not annotations:
        return OCRResult(text="", confidence=None, raw=None)

    return OCRResult(
        text=annotations[0].description or "",
        confidence=None,
        raw=None,  # raw를 통째로 넘기면 직렬화 이슈가 생길 수 있어 기본 None
    )
