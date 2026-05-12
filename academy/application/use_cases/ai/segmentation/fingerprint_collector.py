"""Stage 6.3V (2026-05-07) — LayoutFingerprint 측정 + INSERT wiring.

Stage 6.3P-1 contract 가 정의한 "단계별 보조 입력 분리" 정책 위에서, 운영 doc 처리
종료 시점의 측정값을 LayoutFingerprint 모델에 누적한다. 본 모듈 스스로는 segmentation /
find_similar / router / CLIP 입력 어디에도 영향을 주지 않는다.

원칙 (사용자 directive Stage 6.3V):
- 기존 segmentation 결과 변경 X
- 기존 matching/find_similar 결과 변경 X
- 기존 page_level_fallback / router 정책 변경 X
- 기존 CLIP/OCR 입력 경로 변경 X
- tenant isolation 절대 — fingerprint.tenant = doc.tenant 강제
- 측정 실패는 warning log 만, 문서 처리 흐름 계속
- read-only measurement + insert wiring 만 — schema migration 0

본 모듈은 derived measurement 만. cv2 / OCR / VLM 호출 0. 워커가 callback payload
또는 doc.meta 에 누적해 둔 정보에서 산출한다. 추가 측정값 (skew / OCR conf / YOLO
conf / text_density / line_spacing) 은 다음 stage 에서 워커 측 측정 wiring 후 누적.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# fingerprint schema 버전 — 측정 가능한 필드가 늘어나면 버전 증가
_FINGERPRINT_SCHEMA_VERSION = "v1"


@dataclass
class FingerprintMeasurement:
    """callback / pipeline 종료 시점에서 derived 한 doc-level 측정값.

    LayoutFingerprint 모델 스키마 (tenant FK / document FK / paper_type / page_count /
    page_size / text_density / image_density / column_count / anchor_density /
    x0_clusters / y_gap_distribution / font_size_distribution / filename_patterns /
    similarity_cluster_id) 에 매핑.

    schema 부족 필드 (source_type / segmentation_status / problem_count /
    cropped_problem_count / page_level_fallback / processing_quality / segmentation_method)
    는 filename_patterns 의 단일 dict element 로 namespaced 저장 (다음 stage 에서
    schema migration 으로 정식 column 분리).
    """
    tenant_id: int
    document_id: int
    paper_type: str = ""
    page_count: int = 0
    page_size: Dict[str, Any] = field(default_factory=dict)  # {"width", "height", "dpi"}
    text_density: float = 0.0
    image_density: float = 0.0
    column_count: int = 1
    anchor_density: float = 0.0
    x0_clusters: List[float] = field(default_factory=list)
    y_gap_distribution: Dict[str, Any] = field(default_factory=dict)
    font_size_distribution: Dict[str, Any] = field(default_factory=dict)
    filename_patterns: List[Dict[str, Any]] = field(default_factory=list)
    similarity_cluster_id: str = ""


def measure_from_callback(
    *,
    doc,                                  # MatchupDocument 인스턴스
    result_payload: Dict[str, Any],       # 워커 응답 dict (callback 입력)
    problem_count: int,                   # 재카운트된 실 problem 수
    cropped_problem_count: Optional[int] = None,  # bbox 있는 problem 수 (옵션)
) -> FingerprintMeasurement:
    """callback 종료 시점에서 fingerprint 측정값 산출.

    cv2 / OCR / VLM 호출 0. doc.meta + result_payload + problem_count 에서 derived.
    측정 불가능한 필드는 default (0.0 / [] / {}) — 보고서의 "수집 불가" 섹션 참고.

    Args:
        doc: MatchupDocument (tenant_id / source_type / meta 사용)
        result_payload: 워커 응답 (segmentation_method / paper_type_summary /
            page_dimensions / page_image_keys 등)
        problem_count: bulk_create 후 재카운트된 실 problem 수
        cropped_problem_count: bbox 있는 problem 수 (None 이면 derived 시도)
    """
    meta = dict(doc.meta or {})
    paper_type_summary = meta.get("paper_type_summary") or result_payload.get("paper_type_summary") or {}
    segmentation_method = (
        meta.get("segmentation_method")
        or result_payload.get("segmentation_method")
        or ""
    )
    processing_quality = meta.get("processing_quality") or ""
    bbox_null_ratio = meta.get("bbox_null_ratio")

    # paper_type primary — paper_type_summary 의 most_common
    paper_type_primary = ""
    if isinstance(paper_type_summary, dict):
        primary = paper_type_summary.get("primary") or paper_type_summary.get("most_common")
        if isinstance(primary, str):
            paper_type_primary = primary

    # page_count / page_size — meta.page_dimensions 우선
    page_dims = meta.get("page_dimensions") or result_payload.get("page_dimensions") or []
    page_count = len(page_dims) if page_dims else 0
    page_size: Dict[str, Any] = {}
    if page_dims:
        first = page_dims[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            page_size = {
                "width": int(first[0]),
                "height": int(first[1]),
                # 200dpi PDF 렌더가 운영 표준 (segment_dispatcher.py:164) — 추정값
                "dpi": 200,
            }
        elif isinstance(first, dict):
            page_size = {
                "width": int(first.get("width", 0)),
                "height": int(first.get("height", 0)),
                "dpi": int(first.get("dpi", 200)),
            }

    # column_count — paper_type_summary 안 dual / quad 비율로 추정
    column_count = 1
    if isinstance(paper_type_summary, dict):
        counts = paper_type_summary.get("counts") or {}
        if isinstance(counts, dict):
            dual = sum(v for k, v in counts.items() if isinstance(v, int) and "dual" in str(k))
            quad = sum(v for k, v in counts.items() if isinstance(v, int) and "quad" in str(k))
            if quad > 0:
                column_count = 4
            elif dual > 0:
                column_count = 2

    # filename_patterns 에 schema 부족 메타 저장 (다음 stage schema migration 으로 분리)
    filename_meta = {
        "source_type": str(getattr(doc, "source_type", "") or ""),
        "category": str(getattr(doc, "category", "") or ""),
        "title_excerpt": (str(getattr(doc, "title", "") or ""))[:120],
        "segmentation_method": segmentation_method,
        "processing_quality": processing_quality,
        "page_level_fallback": processing_quality in ("page_fallback", "needs_review", "no_problems"),
        "indexable": bool(meta.get("indexable")) if "indexable" in meta else None,
        "problem_count": int(problem_count),
        "cropped_problem_count": (
            int(cropped_problem_count) if cropped_problem_count is not None else None
        ),
        "bbox_null_ratio": (
            float(bbox_null_ratio) if isinstance(bbox_null_ratio, (int, float)) else None
        ),
        "fingerprint_schema_version": _FINGERPRINT_SCHEMA_VERSION,
    }

    # y_gap_distribution / font_size_distribution — 본 stage 에선 측정 불가 → empty.
    # 다음 stage 에서 워커 segmentation_method 측정 path 에 측정값 누적 후 수집.
    return FingerprintMeasurement(
        tenant_id=int(doc.tenant_id),
        document_id=int(doc.id),
        paper_type=paper_type_primary,
        page_count=page_count,
        page_size=page_size,
        text_density=0.0,            # 다음 stage — page-level 측정 wiring 후
        image_density=0.0,           # 다음 stage
        column_count=column_count,
        anchor_density=0.0,          # 다음 stage
        x0_clusters=[],              # 다음 stage
        y_gap_distribution={},       # 다음 stage — skew / header / footer 측정 후
        font_size_distribution={},   # 다음 stage
        filename_patterns=[filename_meta],
        similarity_cluster_id="",    # 6.3W per-profile 매칭 stage 에서 산출
    )


def save_fingerprint(measurement: FingerprintMeasurement) -> bool:
    """LayoutFingerprint 에 측정값 저장. 실패는 warning log + False 리턴.

    update_or_create 로 idempotent — 동일 (tenant, document, fingerprint_version=1)
    재처리 시 row 1개만 유지 (UniqueConstraint 충돌 회피).

    Returns:
        True: INSERT/UPDATE 성공
        False: 실패 (본 흐름은 절대 영향 받지 않아야 함 — 호출자가 무시)
    """
    try:
        from apps.domains.matchup.models import LayoutFingerprint, MatchupDocument
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "FINGERPRINT_IMPORT_FAIL | doc_id=%s | err=%s",
            measurement.document_id, e,
        )
        return False

    try:
        # tenant + doc 존재 + tenant 일치 재검증 (방어층)
        try:
            doc = MatchupDocument.objects.get(id=measurement.document_id)
        except MatchupDocument.DoesNotExist:
            logger.warning(
                "FINGERPRINT_DOC_NOT_FOUND | doc_id=%s",
                measurement.document_id,
            )
            return False
        if int(doc.tenant_id) != int(measurement.tenant_id):
            logger.error(
                "FINGERPRINT_TENANT_MISMATCH | doc_id=%s | doc_tenant=%s | meas_tenant=%s",
                measurement.document_id, doc.tenant_id, measurement.tenant_id,
            )
            return False

        defaults = {
            "paper_type": measurement.paper_type[:32],
            "page_count": measurement.page_count,
            "page_size": measurement.page_size,
            "text_density": measurement.text_density,
            "image_density": measurement.image_density,
            "column_count": measurement.column_count,
            "anchor_density": measurement.anchor_density,
            "x0_clusters": measurement.x0_clusters,
            "y_gap_distribution": measurement.y_gap_distribution,
            "font_size_distribution": measurement.font_size_distribution,
            "filename_patterns": measurement.filename_patterns,
            "similarity_cluster_id": measurement.similarity_cluster_id,
        }
        LayoutFingerprint.objects.update_or_create(
            tenant_id=measurement.tenant_id,
            document_id=measurement.document_id,
            fingerprint_version=1,
            defaults=defaults,
        )
        logger.info(
            "FINGERPRINT_SAVED | doc_id=%s | tenant=%s | paper_type=%s | "
            "problem_count=%s | quality=%s",
            measurement.document_id, measurement.tenant_id,
            measurement.paper_type,
            measurement.filename_patterns[0].get("problem_count") if measurement.filename_patterns else None,
            measurement.filename_patterns[0].get("processing_quality") if measurement.filename_patterns else None,
        )
        return True
    except Exception as e:  # noqa: BLE001
        # 본 흐름 영향 0 보장 — 어떤 예외도 callback 으로 전파 안 시킨다.
        logger.warning(
            "FINGERPRINT_SAVE_FAIL | doc_id=%s | tenant=%s | err=%s",
            measurement.document_id, measurement.tenant_id, e,
        )
        return False


def collect_and_save(
    *,
    doc,
    result_payload: Dict[str, Any],
    problem_count: int,
    cropped_problem_count: Optional[int] = None,
) -> bool:
    """callback 안에서 호출되는 단일 진입점. 모든 예외를 swallow + log.

    호출자는 본 함수의 반환값을 무시하고 본 흐름을 계속 진행해야 한다.
    """
    try:
        measurement = measure_from_callback(
            doc=doc,
            result_payload=result_payload,
            problem_count=problem_count,
            cropped_problem_count=cropped_problem_count,
        )
        return save_fingerprint(measurement)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "FINGERPRINT_COLLECT_FAIL | doc_id=%s | err=%s",
            getattr(doc, "id", "?"), e,
        )
        return False


__all__ = [
    "FingerprintMeasurement",
    "measure_from_callback",
    "save_fingerprint",
    "collect_and_save",
]
