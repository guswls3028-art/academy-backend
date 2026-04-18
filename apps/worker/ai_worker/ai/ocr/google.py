# apps/worker/ai/ocr/google.py
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional, Tuple

# google cloud vision
from google.cloud import vision  # type: ignore

logger = logging.getLogger(__name__)

# CloudWatch custom metric 상수 — Vision OCR 호출량/에러 추적용.
# 캐시 히트는 lru_cache가 함수 본문 실행을 생략하므로 자동으로 제외됨 (비용 메트릭 의도).
_CW_NAMESPACE = "Academy/AIWorker"
_CW_METRIC_CALLS = "VisionOCRCalls"
_CW_METRIC_ERRORS = "VisionOCRErrors"

_cached_cw_client: Any = None


def _get_cw_client() -> Any:
    """CloudWatch boto3 클라이언트 (lazy, 실패 silent)."""
    global _cached_cw_client
    if _cached_cw_client is not None:
        return _cached_cw_client
    try:
        import boto3  # type: ignore
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-2"
        _cached_cw_client = boto3.client("cloudwatch", region_name=region)
    except Exception as e:  # noqa: BLE001
        logger.debug("CloudWatch client init failed: %s", e)
        _cached_cw_client = False  # sentinel — 재시도 방지
    return _cached_cw_client


def _emit_vision_metric(metric_name: str) -> None:
    """CloudWatch에 Vision OCR 호출/에러 메트릭 put. 실패는 silent — OCR 결과에 영향 없음."""
    try:
        client = _get_cw_client()
        if not client:
            return
        client.put_metric_data(
            Namespace=_CW_NAMESPACE,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": 1,
                    "Unit": "Count",
                }
            ],
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("CloudWatch put_metric_data failed (%s): %s", metric_name, e)


@dataclass
class OCRResult:
    text: str
    confidence: Optional[float] = None
    raw: Optional[Any] = None


@dataclass
class OCRTextBlock:
    """OCR로 추출한 텍스트 블록 (픽셀 좌표계, 단락 단위)."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


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


def google_ocr_blocks(image_path: str) -> List[OCRTextBlock]:
    """
    Vision document_text_detection으로 줄 단위 텍스트 블록을 bbox와 함께 추출.

    문항 세그멘테이션용 — 스캔본 시험지에서 문항 번호 감지에 사용.
    좌표계는 입력 이미지의 픽셀 좌표계.

    동일 경로·동일 파일 크기 조합에 대해 결과를 메모리 캐시 (LRU)하여
    한 번의 작업 내에서 중복 OCR 호출(dispatcher + pipeline)을 방지.
    """
    try:
        stat = os.stat(image_path)
        key: Tuple[str, int, int] = (image_path, int(stat.st_size), int(stat.st_mtime))
    except OSError:
        return []

    return list(_google_ocr_blocks_cached(key))


@lru_cache(maxsize=64)
def _google_ocr_blocks_cached(
    key: Tuple[str, int, int],
) -> Tuple[OCRTextBlock, ...]:
    """(image_path, size, mtime) 튜플 키로 OCR 결과 캐시.

    주의: `lru_cache` 캐시 히트 시 이 함수 본문은 실행되지 않으므로
    CloudWatch 메트릭은 자동으로 캐시 미스(실제 Vision API 호출)에 대해서만 카운트됨.
    이는 비용/쿼터 추적 목적에 부합하는 의도된 동작임.
    """
    image_path = key[0]
    client = _get_vision_client()

    with open(image_path, "rb") as f:
        content = f.read()

    image = vision.Image(content=content)

    try:
        response = client.document_text_detection(image=image)
    except Exception:
        # 예외 경로: 네트워크/쿼터/인증 실패 등. 메트릭 put 후 재raise.
        _emit_vision_metric(_CW_METRIC_ERRORS)
        raise

    # 성공 호출 (response.error가 있어도 API round-trip은 성공 → 호출 카운트는 증가)
    _emit_vision_metric(_CW_METRIC_CALLS)

    if getattr(response, "error", None) and response.error.message:
        # API가 error 필드로 실패를 돌려준 경우 — 에러 메트릭도 기록
        _emit_vision_metric(_CW_METRIC_ERRORS)
        return tuple()

    full_annotation = getattr(response, "full_text_annotation", None)
    if not full_annotation:
        return tuple()

    # 줄 단위로 그룹핑 — detected_break가 LINE_BREAK(5)/EOL_SURE_SPACE(3)일 때 한 줄 종료.
    LINE_BREAK_TYPES = {3, 5}  # EOL_SURE_SPACE, LINE_BREAK

    blocks: List[OCRTextBlock] = []

    for page in full_annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                current_words: List[Any] = []
                for word in paragraph.words:
                    current_words.append(word)

                    last_sym = word.symbols[-1] if word.symbols else None
                    brk_val = 0
                    if last_sym and last_sym.property:
                        det_break = last_sym.property.detected_break
                        if det_break:
                            try:
                                brk_val = int(det_break.type_)  # type: ignore[attr-defined]
                            except (AttributeError, TypeError, ValueError):
                                try:
                                    brk_val = int(det_break.type)  # noqa: E721
                                except (AttributeError, TypeError, ValueError):
                                    brk_val = 0

                    if brk_val in LINE_BREAK_TYPES:
                        tb = _line_to_block(current_words)
                        if tb is not None:
                            blocks.append(tb)
                        current_words = []

                if current_words:
                    tb = _line_to_block(current_words)
                    if tb is not None:
                        blocks.append(tb)

    return tuple(blocks)


def _line_to_block(words: List[Any]) -> Optional[OCRTextBlock]:
    """Vision API word 리스트를 하나의 텍스트 줄(OCRTextBlock)로 변환."""
    if not words:
        return None

    parts: List[str] = []
    xs: List[int] = []
    ys: List[int] = []

    for w in words:
        word_text = "".join(s.text for s in w.symbols)
        if word_text:
            parts.append(word_text)

        bb = getattr(w, "bounding_box", None)
        if bb is None:
            continue
        for v in bb.vertices:
            xs.append(int(v.x or 0))
            ys.append(int(v.y or 0))

    if not xs or not ys:
        return None

    text = " ".join(parts).strip()
    if not text:
        return None

    return OCRTextBlock(
        text=text,
        x0=float(min(xs)),
        y0=float(min(ys)),
        x1=float(max(xs)),
        y1=float(max(ys)),
    )
