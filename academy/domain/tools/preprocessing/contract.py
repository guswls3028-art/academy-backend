"""Stage 6.3P-1 (2026-05-07) — Preprocessing input contract.

Stage 6.3P audit 결과 (manual/auto 비대칭, preprocess_for_detect dead code, manual CLIP
contract 위반) 를 바탕으로 전처리를 "원본 이미지 교체" 가 아니라 "단계별 보조 입력 생성"
으로 분리하는 얇은 contract.

원칙 (사용자 directive Stage 6.3P-1):
- 운영 segment_dispatcher / segment_opencv / segment_yolo / segment_ocr / vlm_fallback
  과 wiring 0 (assert_no_operational_wiring 가드)
- DB write 0 / R2 write 0 / OCR/VLM 실호출 0
- 원본 이미지 overwrite 0
- callback / services / views / cache 미변경
- raw_page_image 항상 보존
- detect_input / ocr_input / embedding_input / vlm_input 분리 — 재사용 금지
- bbox-changing transform (deskew / perspective / resize / scale) 은 transform_metadata
  + inverse_supported=True 동반 시만 허용

본 모듈은 contract 정의 + decision helper + 정책 enum 만. 호출자 wiring 은 별 stage.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────
# source_type — 운영 7-value 라우터 + 본 contract 가 추가로 구분하는 파생
# ─────────────────────────────────────────────────────────────────────────
class SourceType(str, enum.Enum):
    """매치업 source_type 분류.

    운영 enum (services.source_types 참고) 7-value + 본 contract 가 path 분기를
    위해 추가로 구분하는 파생값 (SCANNED_PDF / NATIVE_PDF / IMAGE_ONLY_PAGE).
    파생값은 contract 결정 helper 의 입력으로만 쓰이며, DB column 으로 저장되지
    않는다.
    """
    STUDENT_EXAM_PHOTO = "student_exam_photo"
    SCHOOL_EXAM_PDF = "school_exam_pdf"
    COMMERCIAL_WORKBOOK = "commercial_workbook"
    ACADEMY_WORKBOOK = "academy_workbook"
    EXPLANATION = "explanation"
    ANSWER_KEY = "answer_key"
    OTHER = "other"
    # contract-side derived
    SCANNED_PDF = "scanned_pdf"
    NATIVE_PDF = "native_pdf"
    IMAGE_ONLY_PAGE = "image_only_page"


# ─────────────────────────────────────────────────────────────────────────
# 입력 stage / transform 종류
# ─────────────────────────────────────────────────────────────────────────
class PreprocessingStage(str, enum.Enum):
    RAW = "raw"
    DETECT = "detect"
    OCR = "ocr"
    EMBEDDING = "embedding"
    VLM = "vlm"


class TransformKind(str, enum.Enum):
    NONE = "none"
    GRAYSCALE = "grayscale"
    CONTRAST = "contrast"
    AUTOCONTRAST = "autocontrast"
    BINARY_THRESHOLD = "binary_threshold"
    CLAHE = "clahe"
    DESKEW = "deskew"
    UNSHARP_MASK = "unsharp_mask"
    PERSPECTIVE_RECTIFY = "perspective_rectify"
    RESIZE = "resize"
    SCALE = "scale"


# bbox 좌표를 변경하는 transform — 적용 시 transform_metadata + inverse 필수.
_BBOX_CHANGING_TRANSFORMS: frozenset = frozenset({
    TransformKind.DESKEW,
    TransformKind.PERSPECTIVE_RECTIFY,
    TransformKind.RESIZE,
    TransformKind.SCALE,
})


# stage 별 허용 transform — Stage 6.3P audit 결과 기반.
_ALLOWED_TRANSFORMS_BY_STAGE: dict = {
    PreprocessingStage.RAW: frozenset({TransformKind.NONE}),
    PreprocessingStage.DETECT: frozenset({
        TransformKind.NONE,
        TransformKind.GRAYSCALE,
        TransformKind.CONTRAST,
        TransformKind.AUTOCONTRAST,
        TransformKind.BINARY_THRESHOLD,  # segment_opencv 내부 Otsu 와 동치
        TransformKind.CLAHE,             # mild LAB-CLAHE 후보 (Stage 6.3P dry-run best)
    }),
    PreprocessingStage.OCR: frozenset({
        TransformKind.NONE,
        TransformKind.GRAYSCALE,
        TransformKind.CLAHE,
        TransformKind.DESKEW,            # transform_metadata + inverse 필수
        TransformKind.UNSHARP_MASK,
        TransformKind.SCALE,             # API limit resize — _prepare_image_for_vision
    }),
    PreprocessingStage.EMBEDDING: frozenset({
        # CLIP 은 raw crop 우선. binary / strong contrast / deskew 절대 금지.
        TransformKind.NONE,
        TransformKind.CLAHE,             # mild only — color distribution 보존 한도
    }),
    PreprocessingStage.VLM: frozenset({
        TransformKind.NONE,
        TransformKind.SCALE,             # _GEMINI_VISION_MAX_DIM resize + scale 역변환
        TransformKind.UNSHARP_MASK,
    }),
}


# ─────────────────────────────────────────────────────────────────────────
# page-level fallback 정책 (정의만 — 운영 적용은 별 stage)
# ─────────────────────────────────────────────────────────────────────────
class SegmentationStatus(str, enum.Enum):
    PROBLEM_LEVEL = "problem_level"
    PAGE_LEVEL_FALLBACK = "page_level_fallback"
    FAILED = "failed"


class MatchupQuality(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


# page_level_fallback 의 정책: success 가 아니라 low_quality_fallback.
PAGE_LEVEL_FALLBACK_IS_SUCCESS: bool = False
DIRECT_HIT_LABEL_ALLOWED_FOR_FALLBACK: bool = False


# ─────────────────────────────────────────────────────────────────────────
# transform metadata — 좌표계가 바뀌는 변환은 inverse 인터페이스 명세
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PreprocessTransformMetadata:
    """좌표계가 바뀌는 transform 의 정보 + inverse 인터페이스 명세.

    Stage 6.3P-1: dataclass 만 — 실 inverse 함수는 별 stage (perspective rectification
    Stage 6.3R / scan-only deskew Stage 6.3R 후보) 에서 구현. 본 stage 에선
    inverse_supported=True 인 metadata 도 운영 wiring 차단 (assert_no_operational_wiring).
    """
    transform_kind: TransformKind
    matrix: Optional[Tuple[Tuple[float, ...], ...]] = None  # 2x3 affine 또는 3x3 perspective
    src_shape: Optional[Tuple[int, int]] = None             # (height, width) raw page
    dst_shape: Optional[Tuple[int, int]] = None             # (height, width) transformed
    deskew_angle_deg: float = 0.0
    scale: float = 1.0
    confidence: float = 0.0
    inverse_supported: bool = False                          # inverse_bbox 함수 가용 여부

    def changes_bbox_coordinates(self) -> bool:
        return self.transform_kind in _BBOX_CHANGING_TRANSFORMS

    def is_safe_for_operational_wiring(self) -> bool:
        """운영 연결 가능 여부 — bbox 변경 시 inverse 미구현이면 False."""
        if not self.changes_bbox_coordinates():
            return True
        return self.inverse_supported


# ─────────────────────────────────────────────────────────────────────────
# 5단 입력 분리
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RawPageInput:
    """원본 페이지 입력. 어떤 변환도 적용하지 않는다.

    overwrite 금지 (frozen). image_key 는 R2 read 만 허용 — 본 dataclass 가
    가리키는 byte 는 어떤 stage 에서도 변경되지 않아야 한다.
    """
    image_key: str
    width: int
    height: int
    page_index: Optional[int] = None
    pdf_dpi: Optional[int] = None  # PDF 렌더 dpi (이미지면 None)


@dataclass(frozen=True)
class DetectInputImage:
    """OpenCV / segment_opencv / YOLO 입력 전용. shape = raw 와 동일."""
    raw: RawPageInput
    transforms: Tuple[TransformKind, ...] = ()
    transform_metadata: Optional[PreprocessTransformMetadata] = None

    def validate(self) -> None:
        _validate_transforms(PreprocessingStage.DETECT, self.transforms, self.transform_metadata)


@dataclass(frozen=True)
class OcrInputImage:
    """OCR (Google Vision / Tesseract) 입력 전용.

    binary / strong contrast 금지 (인식률 저하). deskew 시 transform_metadata
    inverse_supported=True 강제 — bbox 응답 후 raw 좌표로 inverse 필요.
    """
    raw: RawPageInput
    transforms: Tuple[TransformKind, ...] = ()
    transform_metadata: Optional[PreprocessTransformMetadata] = None

    def validate(self) -> None:
        _validate_transforms(PreprocessingStage.OCR, self.transforms, self.transform_metadata)


@dataclass(frozen=True)
class EmbeddingInputImage:
    """CLIP / openai image embedding 입력 전용.

    raw crop 우선. binary / threshold / strong contrast 절대 금지 — color
    distribution / edge texture 가 backbone 학습 분포에 가까워야 함.
    Stage 6.3P audit: matchup_manual_index.py:227 가 OCR 와 동일 _preprocess_camera_image
    결과를 CLIP 에 사용 — 본 contract 위반. 후속 stage (6.3S) 에서 backfill.
    """
    raw: RawPageInput
    transforms: Tuple[TransformKind, ...] = ()
    transform_metadata: Optional[PreprocessTransformMetadata] = None

    def validate(self) -> None:
        _validate_transforms(PreprocessingStage.EMBEDDING, self.transforms, self.transform_metadata)


@dataclass(frozen=True)
class VlmInputImage:
    """Gemini Vision 입력 전용.

    현재 운영은 vlm_fallback.py:599~622 에서 SCALE (1600px max + JPEG 85) + bbox
    inverse 만 적용. 본 contract 도 이 범위 그대로 — VLM 자체가 perspective/그림자/
    rotation 처리 능력이 있으므로 강한 deskew 는 권장 X.
    """
    raw: RawPageInput
    transforms: Tuple[TransformKind, ...] = ()
    transform_metadata: Optional[PreprocessTransformMetadata] = None

    def validate(self) -> None:
        _validate_transforms(PreprocessingStage.VLM, self.transforms, self.transform_metadata)


def _validate_transforms(
    stage: PreprocessingStage,
    transforms: Tuple[TransformKind, ...],
    metadata: Optional[PreprocessTransformMetadata],
) -> None:
    """stage 별 허용 transform 검증 + bbox-changing 시 metadata 강제."""
    allowed = _ALLOWED_TRANSFORMS_BY_STAGE[stage]
    for t in transforms:
        if t not in allowed:
            raise InvalidPreprocessingTransform(
                f"transform {t.value} not allowed in stage {stage.value} — "
                f"allowed: {sorted(x.value for x in allowed)}"
            )
    has_bbox_changing = any(t in _BBOX_CHANGING_TRANSFORMS for t in transforms)
    if has_bbox_changing:
        if metadata is None:
            raise MissingTransformMetadata(
                f"bbox-changing transforms in stage {stage.value} require transform_metadata "
                f"with inverse_supported=True"
            )
        if not metadata.is_safe_for_operational_wiring():
            raise UnsafeTransformWiring(
                f"transform {metadata.transform_kind.value} is bbox-changing but "
                f"inverse_supported=False — wiring forbidden until inverse is implemented"
            )


# ─────────────────────────────────────────────────────────────────────────
# 예외
# ─────────────────────────────────────────────────────────────────────────
class PreprocessingContractError(Exception):
    pass


class InvalidPreprocessingTransform(PreprocessingContractError):
    pass


class MissingTransformMetadata(PreprocessingContractError):
    pass


class UnsafeTransformWiring(PreprocessingContractError):
    pass


# ─────────────────────────────────────────────────────────────────────────
# source_type → preprocessing decision
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PreprocessingDecision:
    """source_type / native PDF / text_density 입력에 대한 전처리 정책 결정.

    detect_apply_clahe / ocr_apply_clahe_deskew / embedding_use_raw_crop / vlm_use_raw
    flag 만 — 실 변환은 별 stage adapter 에서. 본 모듈은 정책 결정까지.
    """
    source_type: SourceType
    is_native_pdf: bool
    text_density: Optional[float]
    detect_apply_clahe: bool
    ocr_apply_clahe_deskew: bool
    embedding_use_raw_crop: bool
    vlm_use_raw: bool
    rationale: str

    def camera_preprocessing_allowed(self) -> bool:
        """student_exam_photo / scanned / image-based path 만 True."""
        return self.detect_apply_clahe or self.ocr_apply_clahe_deskew


# Stage 6.3P dry-run 측정 기반 임계 (academy_workbook 0.0232, scanned 0.0517).
TEXT_DENSITY_THRESHOLD: float = 0.025


def decide_preprocessing(
    *,
    source_type: SourceType,
    is_native_pdf: bool,
    text_density: Optional[float] = None,
) -> PreprocessingDecision:
    """전처리 정책 결정.

    원칙:
    - native PDF + 충분한 text density → preprocessing skip (native parser 우선)
    - explanation / answer_key → indexing skip path — preprocessing 무관
    - commercial_workbook → skip_vlm_auto + page fallback path 유지
    - student_exam_photo / scanned 파생 / image_only_page → preprocessing 후보
    - school_exam_pdf → native + text density 충분이면 skip, 아니면 image-based fallback
    - academy_workbook → 보통 native, low density 면 image fallback
    """
    # native + 충분 density → preprocessing skip
    if is_native_pdf and (text_density is None or text_density >= TEXT_DENSITY_THRESHOLD):
        return PreprocessingDecision(
            source_type=source_type,
            is_native_pdf=True,
            text_density=text_density,
            detect_apply_clahe=False,
            ocr_apply_clahe_deskew=False,
            embedding_use_raw_crop=True,
            vlm_use_raw=True,
            rationale="native PDF with sufficient text density — skip preprocessing",
        )

    # indexing skip path
    if source_type in (SourceType.EXPLANATION, SourceType.ANSWER_KEY):
        return PreprocessingDecision(
            source_type=source_type,
            is_native_pdf=is_native_pdf,
            text_density=text_density,
            detect_apply_clahe=False,
            ocr_apply_clahe_deskew=False,
            embedding_use_raw_crop=True,
            vlm_use_raw=True,
            rationale="explanation/answer_key skip path — preprocessing irrelevant",
        )

    # commercial_workbook — skip_vlm_auto + page fallback (Stage 6.3P SSOT 정책)
    if source_type == SourceType.COMMERCIAL_WORKBOOK:
        return PreprocessingDecision(
            source_type=source_type,
            is_native_pdf=is_native_pdf,
            text_density=text_density,
            detect_apply_clahe=False,
            ocr_apply_clahe_deskew=False,
            embedding_use_raw_crop=True,
            vlm_use_raw=True,
            rationale="commercial_workbook skip path",
        )

    # student_exam_photo — 학생 시험지 사진. CLAHE+deskew 후보.
    if source_type == SourceType.STUDENT_EXAM_PHOTO:
        return PreprocessingDecision(
            source_type=source_type,
            is_native_pdf=False,
            text_density=text_density,
            detect_apply_clahe=True,
            ocr_apply_clahe_deskew=True,
            embedding_use_raw_crop=True,  # CLIP 은 raw — manual 경로 위반 fix 후속 (Stage 6.3S)
            vlm_use_raw=True,
            rationale="student_exam_photo image-based path — preprocessing candidate",
        )

    # scanned_pdf / image_only_page (contract-side 파생) — image-based path
    if source_type in (SourceType.SCANNED_PDF, SourceType.IMAGE_ONLY_PAGE):
        return PreprocessingDecision(
            source_type=source_type,
            is_native_pdf=is_native_pdf,
            text_density=text_density,
            detect_apply_clahe=True,
            ocr_apply_clahe_deskew=True,
            embedding_use_raw_crop=True,
            vlm_use_raw=True,
            rationale=f"{source_type.value} image-based path — preprocessing candidate",
        )

    # school_exam_pdf — native 인지 scanned 인지 분기
    if source_type == SourceType.SCHOOL_EXAM_PDF:
        return PreprocessingDecision(
            source_type=source_type,
            is_native_pdf=is_native_pdf,
            text_density=text_density,
            detect_apply_clahe=True,
            ocr_apply_clahe_deskew=True,
            embedding_use_raw_crop=True,
            vlm_use_raw=True,
            rationale=(
                "school_exam_pdf with low text density (likely scanned) — "
                "preprocessing candidate"
            ),
        )

    # academy_workbook — 보통 native (위 분기 통과). 여기 도달했으면 image fallback.
    if source_type == SourceType.ACADEMY_WORKBOOK:
        return PreprocessingDecision(
            source_type=source_type,
            is_native_pdf=is_native_pdf,
            text_density=text_density,
            detect_apply_clahe=not is_native_pdf,
            ocr_apply_clahe_deskew=not is_native_pdf,
            embedding_use_raw_crop=True,
            vlm_use_raw=True,
            rationale=(
                "academy_workbook image-based fallback — preprocessing candidate "
                "only when not native"
            ),
        )

    # OTHER / NATIVE_PDF (파생 sentinel) — 보수적 raw 우선
    return PreprocessingDecision(
        source_type=source_type,
        is_native_pdf=is_native_pdf,
        text_density=text_density,
        detect_apply_clahe=False,
        ocr_apply_clahe_deskew=False,
        embedding_use_raw_crop=True,
        vlm_use_raw=True,
        rationale="conservative default — preprocessing skipped",
    )


# ─────────────────────────────────────────────────────────────────────────
# 운영 wiring guard — Stage 6.3P-1 phase 차단
# ─────────────────────────────────────────────────────────────────────────
def assert_no_operational_wiring(_input_image: object) -> None:
    """본 모듈은 Stage 6.3P-1 phase 의 contract 정의 — 운영 segment_* 와 wiring 차단.

    transform_metadata + inverse 인프라 + scan-only feature flag 가 갖춰지는
    Stage 6.3Q (segment_opencv mild_clahe 통합) / Stage 6.3R (perspective rectification)
    까지 호출자는 본 contract 를 dry-run / test 용으로만 사용해야 한다. 본 함수는
    그 보호 가드 — 운영 wiring 시 explicit 한 raise 로 stage advance 강제.
    """
    raise UnsafeTransformWiring(
        "operational wiring is forbidden in stage 6.3P-1 — "
        "contract is dry-run only until 6.3Q feature flag and "
        "transform inverse infrastructure are in place"
    )
