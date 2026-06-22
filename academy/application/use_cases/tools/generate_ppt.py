# PATH: academy/application/use_cases/tools/generate_ppt.py
# Use case orchestrators for PPT generation.
#
# - GeneratePptUseCase: from pre-processed images (existing flow)
# - GeneratePptFromPdfUseCase: from PDF with question splitting

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional

from PIL import Image as PILImage

logger = logging.getLogger(__name__)


@dataclass
class PptResult:
    """Result of PPT generation."""
    pptx_bytes: bytes
    slide_count: int
    # "question": 문항 단위 분리. "page": 페이지 단위 (스캔 PDF fallback). 이미지 모드는 None.
    mode: Optional[str] = None


@dataclass
class _PdfQuestionPlan:
    """Question regions planned before rendering/cropping PDF pages."""
    use_whole_page: bool
    regions_per_page: List[List[Any]]
    workbook_doc: bool = False


class GeneratePptUseCase:
    """Generate PPT from a list of image byte arrays.

    This is the existing flow: images are already preprocessed on the API side.
    The worker just assembles them into a PPT.
    """

    def execute(
        self,
        image_bytes_list: Iterable[bytes],
        config: Optional[dict] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
        total_count: Optional[int] = None,
    ) -> PptResult:
        """Execute PPT generation from images.

        Args:
        image_bytes_list: Iterable of image bytes (JPEG/PNG).
        config: PPT config dict with keys: aspect_ratio, background, fit_mode.
        on_progress: Optional callback (percent, step_name).
        total_count: Optional count for streaming iterables.

        Returns:
            PptResult with PPTX bytes and slide count.
        """
        from academy.domain.tools.ppt_composer import PptComposer, PptConfig

        cfg = config or {}
        ppt_config = PptConfig(
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            background=cfg.get("background", "black"),
            fit_mode=cfg.get("fit_mode", "contain"),
        )

        composer = PptComposer(ppt_config)
        if total_count is not None:
            total = total_count
        else:
            try:
                total = len(image_bytes_list)  # type: ignore[arg-type]
            except TypeError:
                total = 0

        for idx, img_bytes in enumerate(image_bytes_list):
            composer.add_slide(img_bytes)
            if on_progress:
                pct = int((idx + 1) / total * 100) if total else 0
                label = f"슬라이드 {idx + 1}/{total}" if total else f"슬라이드 {idx + 1}"
                on_progress(pct, label)

        pptx_bytes = composer.finalize()
        return PptResult(pptx_bytes=pptx_bytes, slide_count=composer.slide_count)


class GeneratePptFromPdfUseCase:
    """Generate PPT from a PDF by splitting questions and composing slides.

    Page-by-page streaming: renders one page, extracts questions, crops,
    preprocesses, and adds to PPT. Then releases the page image from memory.
    """

    def execute(
        self,
        pdf_path: str,
        config: Optional[dict] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
        image_settings: Optional[dict] = None,
    ) -> PptResult:
        """Execute PPT generation from PDF.

        Args:
            pdf_path: Path to the PDF file.
            config: PPT config dict with keys: aspect_ratio, background, fit_mode.
            on_progress: Optional callback (percent, step_name).

        Returns:
            PptResult with PPTX bytes and slide count.
        """
        from academy.adapters.tools.pymupdf_renderer import PdfDocument
        from academy.domain.tools.image_preprocessor import preprocess_for_export, trim_bottom_whitespace
        from academy.domain.tools.ppt_composer import PptComposer, PptConfig

        # 빔프로젝터 1080p 충분 + Pillow MAX_IMAGE_PIXELS(50M) 안전. 고해상도 스캔본 대응.
        WHOLE_PAGE_MAX_LONG_EDGE = 2400

        cfg = config or {}
        ppt_config = PptConfig(
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            background=cfg.get("background", "black"),
            fit_mode=cfg.get("fit_mode", "contain"),
        )

        composer = PptComposer(ppt_config)

        def _apply_user_settings(img_bytes: bytes) -> bytes:
            if not image_settings:
                return img_bytes
            if not any([
                image_settings.get("invert"),
                image_settings.get("grayscale"),
                image_settings.get("auto_enhance"),
                float(image_settings.get("brightness", 1.0)) != 1.0,
                float(image_settings.get("contrast", 1.0)) != 1.0,
            ]):
                return img_bytes
            from apps.domains.tools.ppt.services import _process_image
            return _process_image(
                img_bytes,
                invert=bool(image_settings.get("invert", False)),
                grayscale=bool(image_settings.get("grayscale", False)),
                auto_enhance=bool(image_settings.get("auto_enhance", False)),
                brightness=float(image_settings.get("brightness", 1.0)),
                contrast=float(image_settings.get("contrast", 1.0)),
            )

        with PdfDocument(pdf_path) as doc:
            page_count = doc.page_count()
            if on_progress:
                on_progress(0, "문항 구조 분석")
            question_plan = _build_pdf_question_plan(doc)
            use_whole_page = question_plan.use_whole_page
            segmented_question_mode = False

            if use_whole_page:
                if on_progress:
                    on_progress(0, "이미지 문항 영역 분석")
                segmented_slide_count = _add_segmented_pdf_slides_to_composer(
                    pdf_path,
                    composer=composer,
                    apply_user_settings=_apply_user_settings,
                    on_progress=on_progress,
                )
                if segmented_slide_count > 0:
                    use_whole_page = False
                    segmented_question_mode = True
                    logger.info(
                        "PPT_PDF_IMAGE_SEGMENTATION_USED slides=%d path=%s",
                        segmented_slide_count,
                        pdf_path,
                    )

            if not segmented_question_mode:
                for page_idx in range(page_count):
                    if on_progress:
                        pct = int(page_idx / max(page_count, 1) * 100)
                        on_progress(pct, f"페이지 {page_idx + 1}/{page_count}")

                    if use_whole_page:
                        # 스캔/이미지 PDF: 이미지 세그멘테이션도 실패하면 페이지 단위로 안전 fallback.
                        page_img = doc.render_page(page_idx, dpi=200)
                        if max(page_img.size) > WHOLE_PAGE_MAX_LONG_EDGE:
                            page_img.thumbnail(
                                (WHOLE_PAGE_MAX_LONG_EDGE, WHOLE_PAGE_MAX_LONG_EDGE),
                                resample=PILImage.LANCZOS,
                            )
                        export_img = preprocess_for_export(page_img)
                        # 페이지 단위 = 사진 컨텐츠. JPEG가 PNG보다 1/3 사이즈 (50p PDF에 critical).
                        img_bytes = _image_to_bytes(export_img, fmt="JPEG")
                        img_bytes = _apply_user_settings(img_bytes)
                        composer.add_slide(img_bytes)
                        del page_img, export_img
                        continue

                    # 텍스트 PDF: pre-pass에서 page type/workbook/cross-page 검증까지 끝낸 문항 crop.
                    regions = (
                        question_plan.regions_per_page[page_idx]
                        if page_idx < len(question_plan.regions_per_page)
                        else []
                    )
                    if not regions:
                        continue

                    page_img = doc.render_page(page_idx, dpi=200)
                    img_w, img_h = page_img.size
                    page_w, page_h = doc.page_dimensions(page_idx)
                    scale_x = img_w / page_w if page_w > 0 else 1.0
                    scale_y = img_h / page_h if page_h > 0 else 1.0

                    for region in regions:
                        rx0, ry0, rx1, ry1 = region.bbox
                        px0 = max(0, int(rx0 * scale_x))
                        py0 = max(0, int(ry0 * scale_y))
                        px1 = min(img_w, int(rx1 * scale_x))
                        py1 = min(img_h, int(ry1 * scale_y))
                        if px1 - px0 < 10 or py1 - py0 < 10:
                            continue
                        crop = page_img.crop((px0, py0, px1, py1))
                        crop = trim_bottom_whitespace(crop, padding_px=12)
                        export_img = preprocess_for_export(crop)
                        img_bytes = _image_to_bytes(export_img)
                        img_bytes = _apply_user_settings(img_bytes)
                        composer.add_slide(img_bytes)
                        del crop, export_img
                    del page_img

            # 텍스트 모드로 갔지만 0 슬라이드인 경우 (예: 모든 페이지가 표지/목차로 분류된 short PDF):
            # 페이지 단위 fallback로 한 번 더 시도해야 사용자가 빈 결과를 보지 않음.
            fallback_triggered = False
            if composer.slide_count == 0 and not use_whole_page:
                logger.info("PDF question-splitting yielded 0 slides - falling back to whole-page mode")
                fallback_triggered = True
                for page_idx in range(page_count):
                    if on_progress:
                        on_progress(50 + int(page_idx / max(page_count, 1) * 50), f"페이지 단위 변환 {page_idx + 1}/{page_count}")
                    page_img = doc.render_page(page_idx, dpi=200)
                    if max(page_img.size) > WHOLE_PAGE_MAX_LONG_EDGE:
                        page_img.thumbnail(
                            (WHOLE_PAGE_MAX_LONG_EDGE, WHOLE_PAGE_MAX_LONG_EDGE),
                            resample=PILImage.LANCZOS,
                        )
                    export_img = preprocess_for_export(page_img)
                    img_bytes = _image_to_bytes(export_img, fmt="JPEG")
                    img_bytes = _apply_user_settings(img_bytes)
                    composer.add_slide(img_bytes)
                    del page_img, export_img

        if composer.slide_count == 0:
            raise ValueError("No slides could be generated from the PDF")

        if on_progress:
            on_progress(100, "완료")

        pptx_bytes = composer.finalize()
        result_mode = "page" if (use_whole_page or fallback_triggered) else "question"
        return PptResult(
            pptx_bytes=pptx_bytes,
            slide_count=composer.slide_count,
            mode=result_mode,
        )


def _build_pdf_question_plan(doc: Any) -> _PdfQuestionPlan:
    """Plan PDF question regions using the Matchup splitter's durable guards.

    The old PPT path called split_questions page-by-page. That skipped page type
    classification, workbook marginal-anchor detection, and cross-page anchor
    validation, so PPT splitting lagged behind the Matchup path. This pre-pass
    keeps only lightweight text blocks in memory, then renders pages later.
    """
    from academy.domain.tools.paper_type import classify_paper_type
    from academy.domain.tools.question_splitter import (
        TextBlock as SplitterTextBlock,
        count_marginal_anchor_candidates,
        split_questions,
        validate_anchors_across_pages,
    )

    phase1: List[dict[str, Any]] = []
    total_text_chars = 0
    page_count = doc.page_count()

    for page_idx in range(page_count):
        try:
            raw_blocks = doc.extract_text_blocks(page_idx)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PPT_PDF_TEXT_EXTRACT_ERROR page=%d error=%s",
                page_idx,
                exc,
            )
            raw_blocks = []

        splitter_blocks = [
            SplitterTextBlock(text=b.text, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
            for b in raw_blocks
        ]
        total_text_chars += sum(len(b.text or "") for b in splitter_blocks)

        try:
            page_w, page_h = doc.page_dimensions(page_idx)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PPT_PDF_DIMENSION_ERROR page=%d error=%s",
                page_idx,
                exc,
            )
            page_w, page_h = 0.0, 0.0

        paper_type_result = None
        is_skip_page = False
        if splitter_blocks and page_w > 0 and page_h > 0:
            try:
                paper_type_result = classify_paper_type(
                    text_blocks=splitter_blocks,
                    page_width=page_w,
                    page_height=page_h,
                    has_embedded_text=True,
                )
                is_skip_page = paper_type_result.is_non_question
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PPT_PDF_CLASSIFY_ERROR page=%d error=%s",
                    page_idx,
                    exc,
                )

        phase1.append({
            "page_index": page_idx,
            "text_blocks": splitter_blocks,
            "page_width": page_w,
            "page_height": page_h,
            "paper_type_result": paper_type_result,
            "is_skip_page": is_skip_page,
        })

    # 스캔/사진 PDF는 text layer가 없어서 문항 split이 불가능하다. 페이지 단위 fallback.
    # 텍스트가 짧은 PDF라도 실제 문항 anchor가 있을 수 있으므로, 글자 수가 적다는
    # 이유만으로 page mode로 보내지 않는다.
    if total_text_chars <= 0:
        return _PdfQuestionPlan(
            use_whole_page=True,
            regions_per_page=[[] for _ in range(page_count)],
        )

    eligible_pages = 0
    pages_with_marginal = 0
    for page in phase1:
        if (
            page["is_skip_page"]
            or not page["text_blocks"]
            or page["page_width"] <= 0
            or page["page_height"] <= 0
        ):
            continue
        eligible_pages += 1
        marginal_count = count_marginal_anchor_candidates(
            page["text_blocks"],
            page["page_width"],
        )
        if marginal_count >= 1:
            pages_with_marginal += 1

    signal_a = False
    if eligible_pages >= 5:
        signal_a = (
            pages_with_marginal >= 3
            and (pages_with_marginal / eligible_pages) >= 0.3
        )
    elif eligible_pages >= 2:
        signal_a = (
            pages_with_marginal >= 2
            and (pages_with_marginal / eligible_pages) >= 0.5
        )

    first_pass_regions: List[List[Any]] = []
    for page in phase1:
        if (
            page["is_skip_page"]
            or not page["text_blocks"]
            or page["page_width"] <= 0
            or page["page_height"] <= 0
        ):
            first_pass_regions.append([])
            continue
        try:
            regions = split_questions(
                page["text_blocks"],
                page["page_width"],
                page["page_height"],
                page_index=page["page_index"],
                paper_type=page["paper_type_result"],
                prefer_marginal=False,
            )
            first_pass_regions.append(list(regions))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PPT_PDF_SPLIT_FIRST_PASS_ERROR page=%d error=%s",
                page["page_index"],
                exc,
            )
            first_pass_regions.append([])

    pages_with_low_anchor = sum(
        1 for regions in first_pass_regions
        if {r.number for r in regions} & {1, 2, 3}
    )
    eligible_with_anchors = sum(1 for regions in first_pass_regions if regions)
    pages_per_number: dict[int, int] = {}
    for regions in first_pass_regions:
        for number in {r.number for r in regions}:
            pages_per_number[number] = pages_per_number.get(number, 0) + 1
    signal_b = False
    if eligible_with_anchors >= 5:
        signal_b = (
            pages_with_low_anchor >= 3
            and (pages_with_low_anchor / eligible_with_anchors) >= 0.3
        )
    elif eligible_with_anchors >= 2:
        repeated_low_numbers = sum(
            1
            for number, count in pages_per_number.items()
            if number in {1, 2, 3} and count >= 2
        )
        all_anchor_pages_single_q1 = all(
            len(regions) == 1 and regions[0].number == 1
            for regions in first_pass_regions
            if regions
        )
        signal_b = (
            pages_with_low_anchor == eligible_with_anchors
            and pages_per_number.get(1, 0) == eligible_with_anchors
            and (repeated_low_numbers >= 2 or all_anchor_pages_single_q1)
        )

    workbook_doc = signal_a or signal_b
    planned_regions: List[List[Any]] = []
    for idx, page in enumerate(phase1):
        if (
            page["is_skip_page"]
            or not page["text_blocks"]
            or page["page_width"] <= 0
            or page["page_height"] <= 0
        ):
            planned_regions.append([])
            continue
        if not workbook_doc:
            planned_regions.append(first_pass_regions[idx])
            continue
        try:
            regions = split_questions(
                page["text_blocks"],
                page["page_width"],
                page["page_height"],
                page_index=page["page_index"],
                paper_type=page["paper_type_result"],
                prefer_marginal=True,
            )
            planned_regions.append(list(regions))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PPT_PDF_SPLIT_WORKBOOK_PASS_ERROR page=%d error=%s",
                page["page_index"],
                exc,
            )
            planned_regions.append(first_pass_regions[idx])

    validated_regions = validate_anchors_across_pages(
        planned_regions,
        force_per_page_restart=workbook_doc,
    )
    logger.info(
        "PPT_PDF_QUESTION_PLAN pages=%d eligible=%d anchors=%d "
        "marginal_pages=%d workbook=%s slides=%d",
        page_count,
        eligible_pages,
        eligible_with_anchors,
        pages_with_marginal,
        workbook_doc,
        sum(len(regions) for regions in validated_regions),
    )
    return _PdfQuestionPlan(
        use_whole_page=False,
        regions_per_page=validated_regions,
        workbook_doc=workbook_doc,
    )


def _add_segmented_pdf_slides_to_composer(
    pdf_path: str,
    *,
    composer: Any,
    apply_user_settings: Callable[[bytes], bytes],
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> int:
    """Use the Matchup image segmentation path when a PDF has no text layer."""
    from academy.adapters.ai.detection.segment_dispatcher import (
        cleanup_pdf_seg_tmp_dirs,
        segment_questions_multipage,
    )
    from academy.domain.tools.image_preprocessor import (
        preprocess_for_export,
        trim_bottom_whitespace,
    )

    result: dict[str, Any] | None = None
    try:
        result = segment_questions_multipage(pdf_path)
        pages = list(result.get("pages") or [])
        total_pages = len(pages)
        added = 0
        for page_idx, page in enumerate(pages):
            boxes = list(page.get("boxes") or [])
            if not boxes:
                continue
            image_path = page.get("image_path")
            if not image_path:
                continue
            if on_progress:
                pct = int(page_idx / max(total_pages, 1) * 100)
                on_progress(pct, f"문항 슬라이드 {page_idx + 1}/{total_pages}")
            with PILImage.open(image_path) as source_img:
                page_img = source_img.convert("RGB")
            try:
                img_w, img_h = page_img.size
                for box in boxes:
                    x, y, w, h = box
                    px0 = max(0, int(x))
                    py0 = max(0, int(y))
                    px1 = min(img_w, int(x + w))
                    py1 = min(img_h, int(y + h))
                    if px1 - px0 < 10 or py1 - py0 < 10:
                        continue
                    crop = page_img.crop((px0, py0, px1, py1))
                    crop = trim_bottom_whitespace(crop, padding_px=12)
                    export_img = preprocess_for_export(crop)
                    img_bytes = _image_to_bytes(export_img)
                    img_bytes = apply_user_settings(img_bytes)
                    composer.add_slide(img_bytes)
                    added += 1
                    del crop, export_img
            finally:
                page_img.close()
        return added
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "PPT_PDF_IMAGE_SEGMENTATION_FALLBACK_ERROR path=%s error=%s",
            pdf_path,
            exc,
        )
        return 0
    finally:
        if result is not None:
            cleanup_pdf_seg_tmp_dirs(list(result.get("tmp_dirs") or []))


def _image_to_bytes(img, fmt: str = "PNG") -> bytes:
    """Convert PIL Image to bytes. JPEG quality 90 for photo content."""
    buf = io.BytesIO()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if fmt == "JPEG":
        if img.mode == "L":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=90, optimize=True, subsampling=0)
    else:
        img.save(buf, format=fmt, optimize=True)
    buf.seek(0)
    return buf.read()
