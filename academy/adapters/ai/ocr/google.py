# apps/worker/ai/ocr/google.py
from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional, Tuple

# google cloud vision
from google.cloud import vision  # type: ignore

logger = logging.getLogger(__name__)

# ── R2 영구 OCR 캐시 ─────────────────────────────────────────────
# 사고 컨텍스트 (2026-04-29): 사용자 GCP Vision 청구 ~37,000원/월. 분석 결과
# retry/reanalyze 시마다 같은 페이지 이미지를 다시 OCR 호출하는 게 큰 비중.
# image bytes의 sha256 hash 기반 영구 캐시로 동일 페이지 재호출 0건 보장.
#
# 캐시 키 구조: ocr-cache/{kind}/{hash[:2]}/{hash}.json
#   - kind: text (google_ocr) / blocks (google_ocr_blocks)
#   - hash[:2]: prefix shard (R2 listing 부담 분산)
#
# Hit 비용: R2 GET (~$0.36/M req) << Vision API ($1.50/1K req). 1000회 재호출 막으면 약 1,800원 절감,
# R2 비용은 사실상 무시 가능.

_OCR_CACHE_ENABLED = os.environ.get("MATCHUP_OCR_R2_CACHE", "1") != "0"


def _ocr_cache_key(image_bytes: bytes, kind: str) -> str:
    import hashlib
    h = hashlib.sha256(image_bytes).hexdigest()
    return f"ocr-cache/{kind}/{h[:2]}/{h}.json"


def _ocr_cache_get(image_bytes: bytes, kind: str) -> Any:
    if not _OCR_CACHE_ENABLED:
        return None
    try:
        from apps.infrastructure.storage.r2 import get_object_bytes_r2_storage
    except ImportError:
        return None
    try:
        body = get_object_bytes_r2_storage(key=_ocr_cache_key(image_bytes, kind))
        if not body:
            return None
        return json.loads(body)
    except Exception:
        logger.debug("OCR cache get failed", exc_info=True)
        return None


def _ocr_cache_put(image_bytes: bytes, kind: str, payload: Any) -> None:
    if not _OCR_CACHE_ENABLED:
        return
    try:
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
        import io
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        upload_fileobj_to_r2_storage(
            fileobj=io.BytesIO(data),
            key=_ocr_cache_key(image_bytes, kind),
            content_type="application/json",
        )
    except Exception:
        logger.debug("OCR cache put failed", exc_info=True)

# Vision API circuit breaker — 빌링/인증 실패가 한 번 발생하면 일정 시간 차단.
# 사고 컨텍스트 (2026-04-29): GCP project 빌링이 끊겨 PermissionDenied 발생 시
# 매 페이지마다 5~10초 응답 지연이 누적되어 SQS_JOB_TIMEOUT_60MIN으로 워커 stuck 26건.
# 첫 실패 후 cooldown 동안 OCR 호출을 즉시 skip → 매치업 파이프라인은 OpenCV/페이지
# 폴백으로 진행 → worker는 다음 잡으로 넘어감. 빌링 풀리면 cooldown 후 자동 복구.
#
# 4-28 재발 사고 (37,915 retry 폭주, 5만원 spike): cooldown 5분이 너무 짧아 SQS 메시지
# visibility timeout(60분)과 mismatch. 메시지가 visibility 만료 후 재dispatch되어
# 새 워커가 또 시도 → 5분 cooldown 풀리면 또 호출. 30분으로 확장하여
# SQS visibility timeout 절반 이상 cover. ENV로 override 가능.
_AUTH_FAIL_UNTIL = 0.0
_AUTH_FAIL_COOLDOWN_SEC = int(os.environ.get("VISION_OCR_AUTH_FAIL_COOLDOWN_SEC", "1800"))


def _is_auth_disabled_now() -> bool:
    import time
    if time.time() < _AUTH_FAIL_UNTIL:
        # circuit open 상태에서 호출 시도 = retry 폭주 시그널. 카운트.
        _emit_vision_metric(_CW_METRIC_AUTH_FAILS)
        return True
    return False


def _trip_auth_breaker(reason: str) -> None:
    import time
    global _AUTH_FAIL_UNTIL
    _AUTH_FAIL_UNTIL = max(_AUTH_FAIL_UNTIL, time.time() + _AUTH_FAIL_COOLDOWN_SEC)
    _emit_vision_metric(_CW_METRIC_AUTH_FAILS)
    logger.error(
        "VISION_OCR_CIRCUIT_OPEN | cooldown=%ds | reason=%s",
        _AUTH_FAIL_COOLDOWN_SEC, reason,
    )

# Google Vision API 제한:
# - JSON 요청 전체: 20MB (base64 오버헤드 감안 ~15MB 실제)
# - 이미지 dimension: width*height ≤ 75M pixels (공식), 실측 초과 시 silent reject.
# 폰 카메라로 찍은 PDF(3024x4032)를 200dpi로 렌더하면 8400x11200 = 94M pixels.
# PNG 압축 효율로 파일 크기는 7-8MB로 줄지만 dimension 한도 넘어서 Vision이 거부.
# 두 조건 모두 체크해야 함.
_MAX_VISION_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_VISION_PIXELS = 60 * 1_000_000  # 공식 75M보다 보수적 여유 확보
# Resize 시 목표 max dimension — 4000px면 long side 4000x3000 = 12M pixels로 안전.
_MAX_IMAGE_DIMENSION = 4000


def _prepare_image_for_vision(image_path: str) -> bytes:
    """Vision API 제한 내로 이미지 바이트 준비.

    두 조건 체크:
      1) 파일 크기 ≤ 10MB (20MB JSON 한도 여유)
      2) dimension width*height ≤ 60M pixels (공식 75M 여유)
    둘 중 하나라도 넘으면 PIL resize + JPEG 재인코딩.
    """
    with open(image_path, "rb") as f:
        content = f.read()

    try:
        from PIL import Image  # type: ignore
    except ImportError:
        if len(content) > _MAX_VISION_IMAGE_BYTES:
            logger.warning(
                "OCR_IMAGE_OVERSIZE | path=%s | size=%d | PIL unavailable",
                image_path, len(content),
            )
        return content

    # Dimension 확인 — 파일 크기 먼저 체크하면 놓칠 수 있음
    try:
        img = Image.open(io.BytesIO(content))
        w, h = img.size
    except Exception as e:
        logger.warning("OCR_IMAGE_OPEN_FAIL | path=%s | error=%s", image_path, e)
        return content

    pixels = w * h
    size_ok = len(content) <= _MAX_VISION_IMAGE_BYTES
    pixels_ok = pixels <= _MAX_VISION_PIXELS

    if size_ok and pixels_ok:
        return content

    # Resize + 재인코딩 필요
    if img.mode != "RGB":
        img = img.convert("RGB")

    if max(img.size) > _MAX_IMAGE_DIMENSION:
        scale = _MAX_IMAGE_DIMENSION / max(img.size)
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)),
            Image.LANCZOS,
        )

    for quality in (85, 70, 55):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        if len(out) <= _MAX_VISION_IMAGE_BYTES:
            logger.info(
                "OCR_IMAGE_COMPRESSED | path=%s | orig=%dKB(%dx%d) | new=%dKB(%dx%d) | q=%d",
                image_path, len(content) // 1024, w, h,
                len(out) // 1024, img.width, img.height, quality,
            )
            return out

    logger.warning(
        "OCR_IMAGE_STILL_LARGE | path=%s | compressed=%dKB | sending anyway",
        image_path, len(out) // 1024,
    )
    return out

# CloudWatch custom metric 상수 — Vision OCR 호출량/에러 추적용.
# 캐시 히트는 lru_cache가 함수 본문 실행을 생략하므로 자동으로 제외됨 (비용 메트릭 의도).
_CW_NAMESPACE = "Academy/AIWorker"
_CW_METRIC_CALLS = "VisionOCRCalls"
_CW_METRIC_ERRORS = "VisionOCRErrors"
# 비용 절감 효과 모니터링 — R2 영구 캐시 hit 횟수. (CALLS = miss + 실 호출)
_CW_METRIC_CACHE_HITS = "VisionOCRCacheHits"
# 빌링 사고/인증 실패 시도 횟수 — circuit open 후 skip 호출도 카운트.
# 4-28 사고 (37,915 retry 폭주) 모니터링용. CALLS와 별도라 비용 spike 사전 경보.
_CW_METRIC_AUTH_FAILS = "VisionOCRAuthFails"

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
    """CloudWatch에 Vision OCR 호출/에러 메트릭 put. 실패는 silent — OCR 결과에 영향 없음.

    tenant context가 설정되어 있으면 TenantId 차원도 함께 emit하여
    어느 학원이 호출량의 대부분을 차지하는지 대시보드에서 분리 가능.
    """
    try:
        client = _get_cw_client()
        if not client:
            return

        tenant_id_str: Optional[str] = None
        try:
            from apps.core.tenant.context import get_current_tenant  # type: ignore
            tenant = get_current_tenant()
            if tenant is not None:
                tenant_id_str = str(tenant.id)
        except Exception:  # noqa: BLE001
            tenant_id_str = None

        metrics: List[dict] = [
            {"MetricName": metric_name, "Value": 1, "Unit": "Count"},
        ]
        if tenant_id_str:
            metrics.append({
                "MetricName": metric_name,
                "Value": 1,
                "Unit": "Count",
                "Dimensions": [{"Name": "TenantId", "Value": tenant_id_str}],
            })
        client.put_metric_data(Namespace=_CW_NAMESPACE, MetricData=metrics)
    except Exception as e:  # noqa: BLE001
        logger.debug("CloudWatch put_metric_data failed (%s): %s", metric_name, e)


def _track_ocr_usage() -> None:
    """ai_usage 테이블에 ocr 호출 카운트. tracking off / tenant 미설정 시 no-op.

    enforcement off 상태에서도 카운트는 누적되므로,
    나중에 AI_QUOTA_ENFORCEMENT_ENABLED=true 토글로 brake 즉시 활성화 가능.
    AIQuotaExceeded는 그대로 raise — enforcement 활성화 시 실제 brake로 동작.
    """
    try:
        from apps.domains.ai.services.quota import consume_ai_quota
        consume_ai_quota(kind="ocr")
    except ImportError:
        return
    # AIQuotaExceeded는 catch하지 않고 호출자에게 전파 (enforcement 토글 시 brake).
    # 그 외 예외는 quota 모듈 내부에서 swallow됨 (DB 일시 단절 등).


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

    매 호출 = Vision API 1 unit 과금. CloudWatch metric으로 실시간 가시성 확보.
    R2 영구 캐시(image bytes sha256)로 retry/reanalyze 비용 0건.
    """
    if _is_auth_disabled_now():
        return OCRResult(text="", confidence=None, raw={"error": "circuit_open"})

    content = _prepare_image_for_vision(image_path)

    # R2 영구 캐시 확인
    cached = _ocr_cache_get(content, kind="text")
    if cached is not None:
        _emit_vision_metric(_CW_METRIC_CACHE_HITS)
        return OCRResult(
            text=cached.get("text", "") or "",
            confidence=cached.get("confidence"),
            raw=None,
        )

    # ai_usage 누적 (enforcement off 시 카운트만, on 시 한도 초과면 raise).
    # 캐시 히트는 과금 단위가 아니므로 위쪽에서 return 후 카운트하지 않는다.
    _track_ocr_usage()

    client = _get_vision_client()
    image = vision.Image(content=content)
    try:
        response = client.text_detection(image=image)
    except Exception as e:
        _emit_vision_metric(_CW_METRIC_ERRORS)
        if _is_auth_failure(e):
            _trip_auth_breaker(str(e)[:200])
        raise

    # API round-trip 성공 = 호출 카운트 (과금 단위와 매칭)
    _emit_vision_metric(_CW_METRIC_CALLS)

    if getattr(response, "error", None) and response.error.message:
        _emit_vision_metric(_CW_METRIC_ERRORS)
        if _is_auth_failure_message(response.error.message):
            _trip_auth_breaker(response.error.message[:200])
        return OCRResult(text="", confidence=None, raw={"error": response.error.message})

    annotations = getattr(response, "text_annotations", None) or []
    if not annotations:
        # 빈 결과도 캐시 — 같은 빈 이미지 다시 호출 막기
        _ocr_cache_put(content, kind="text", payload={"text": ""})
        return OCRResult(text="", confidence=None, raw=None)

    text_out = annotations[0].description or ""
    _ocr_cache_put(content, kind="text", payload={"text": text_out})

    return OCRResult(
        text=text_out,
        confidence=None,
        raw=None,
    )


def _is_auth_failure(exc: Exception) -> bool:
    """gRPC PermissionDenied/Unauthenticated/BillingDisabled 류 에러 식별."""
    msg = (str(exc) or "").lower()
    cls = type(exc).__name__
    return (
        cls in ("PermissionDenied", "Unauthenticated", "Forbidden")
        or "billing_disabled" in msg
        or "billing is enabled" in msg
        or "permission_denied" in msg
        or "unauthenticated" in msg
        or "403" in msg and "vision" in msg
    )


def _is_auth_failure_message(msg: str) -> bool:
    m = (msg or "").lower()
    return (
        "billing_disabled" in m
        or "permission denied" in m
        or "permission_denied" in m
        or "unauthenticated" in m
    )


def google_ocr_blocks(image_path: str) -> List[OCRTextBlock]:
    """
    Vision document_text_detection으로 줄 단위 텍스트 블록을 bbox와 함께 추출.

    문항 세그멘테이션용 — 스캔본 시험지에서 문항 번호 감지에 사용.
    좌표계는 입력 이미지의 픽셀 좌표계.

    동일 경로·동일 파일 크기 조합에 대해 결과를 메모리 캐시 (LRU)하여
    한 번의 작업 내에서 중복 OCR 호출(dispatcher + pipeline)을 방지.
    """
    if _is_auth_disabled_now():
        return []

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
    content = _prepare_image_for_vision(image_path)

    # R2 영구 캐시 확인 — retry/reanalyze 시 같은 페이지 이미지 재호출 0건
    cached = _ocr_cache_get(content, kind="blocks")
    if cached is not None:
        _emit_vision_metric(_CW_METRIC_CACHE_HITS)
        return tuple(
            OCRTextBlock(
                text=row.get("text", ""),
                x0=float(row.get("x0", 0)), y0=float(row.get("y0", 0)),
                x1=float(row.get("x1", 0)), y1=float(row.get("y1", 0)),
            )
            for row in (cached.get("blocks") or [])
        )

    # ai_usage 누적 (캐시 히트는 카운트하지 않음 — 과금 단위와 일치).
    _track_ocr_usage()

    client = _get_vision_client()
    image = vision.Image(content=content)

    try:
        response = client.document_text_detection(image=image)
    except Exception as e:
        # 예외 경로: 네트워크/쿼터/인증 실패 등. 메트릭 put 후 재raise.
        _emit_vision_metric(_CW_METRIC_ERRORS)
        if _is_auth_failure(e):
            _trip_auth_breaker(str(e)[:200])
            return tuple()  # 인증 실패는 raise하지 않고 빈 결과 — 워커 누적 timeout 방지
        raise

    # 성공 호출 (response.error가 있어도 API round-trip은 성공 → 호출 카운트는 증가)
    _emit_vision_metric(_CW_METRIC_CALLS)

    if getattr(response, "error", None) and response.error.message:
        # API가 error 필드로 실패를 돌려준 경우 — 에러 메트릭도 기록
        _emit_vision_metric(_CW_METRIC_ERRORS)
        if _is_auth_failure_message(response.error.message):
            _trip_auth_breaker(response.error.message[:200])
        return tuple()

    full_annotation = getattr(response, "full_text_annotation", None)
    if not full_annotation:
        _ocr_cache_put(content, kind="blocks", payload={"blocks": []})
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

    # 영구 캐시 — 같은 페이지 이미지 재처리 시 Vision 호출 0건
    _ocr_cache_put(
        content, kind="blocks",
        payload={
            "blocks": [
                {"text": b.text, "x0": b.x0, "y0": b.y0, "x1": b.x1, "y1": b.y1}
                for b in blocks
            ],
        },
    )
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
