# PATH: academy/application/use_cases/tools/generate_ppt.py
# Use case orchestrators for PPT generation.
#
# - GeneratePptUseCase: from pre-processed images (existing flow)
# - GeneratePptFromPdfUseCase: from PDF with question splitting

from __future__ import annotations

import io
import logging
import os
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, List, Optional

from PIL import Image as PILImage
from PIL import ImageDraw as PILImageDraw

logger = logging.getLogger(__name__)


@dataclass
class PptResult:
    """Result of PPT generation."""
    pptx_bytes: bytes
    slide_count: int
    # "question": 문항 단위 분리. "page": 페이지 단위 (스캔 PDF fallback). 이미지 모드는 None.
    mode: Optional[str] = None
    narrow_slide_numbers: List[int] = field(default_factory=list)


@dataclass
class _PdfQuestionPlan:
    """Question regions planned before rendering/cropping PDF pages."""
    use_whole_page: bool
    regions_per_page: List[List[Any]]
    workbook_doc: bool = False
    allow_image_segmentation: bool = True
    fallback_page_indices: List[int] = field(default_factory=list)
    scan_page_indices: List[int] = field(default_factory=list)


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
        return PptResult(
            pptx_bytes=pptx_bytes,
            slide_count=composer.slide_count,
            narrow_slide_numbers=composer.narrow_slide_numbers,
        )


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
        from academy.domain.tools.image_preprocessor import (
            compact_internal_whitespace,
            preprocess_for_export,
            reflow_tall_question,
            trim_bottom_whitespace,
        )
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

        with PdfDocument(pdf_path) as doc, ExitStack() as scan_scope:
            page_count = doc.page_count()
            if on_progress:
                on_progress(0, "문항 구조 분석")
            question_plan = _build_pdf_question_plan(doc)
            use_whole_page = question_plan.use_whole_page
            fallback_pages = set(question_plan.fallback_page_indices)
            segmented_question_mode = False
            mixed_scan_pages = {}
            if question_plan.scan_page_indices:
                if on_progress:
                    on_progress(0, "스캔 문항 영역 분석")
                mixed_scan_pages = scan_scope.enter_context(
                    _load_pdf_segmentation(pdf_path, page_count, question_plan.scan_page_indices)
                )

            def add_whole_page_slide(page_idx: int) -> None:
                page_img = doc.render_page(page_idx, dpi=200)
                if max(page_img.size) > WHOLE_PAGE_MAX_LONG_EDGE:
                    page_img.thumbnail(
                        (WHOLE_PAGE_MAX_LONG_EDGE, WHOLE_PAGE_MAX_LONG_EDGE),
                        resample=PILImage.LANCZOS,
                    )
                export_img = preprocess_for_export(page_img)
                img_bytes = _apply_user_settings(_image_to_bytes(export_img, fmt="JPEG"))
                composer.add_slide(img_bytes)
                del page_img, export_img

            if use_whole_page and question_plan.allow_image_segmentation:
                if on_progress:
                    on_progress(0, "이미지 문항 영역 분석")
                segmented_slide_count = _add_segmented_pdf_slides_to_composer(
                    pdf_path,
                    composer=composer,
                    apply_user_settings=_apply_user_settings,
                    on_progress=on_progress,
                    expected_page_count=page_count,
                    fallback_pages=fallback_pages,
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

                    if use_whole_page or page_idx in fallback_pages:
                        scan_page = mixed_scan_pages.get(page_idx)
                        if scan_page is not None:
                            crops = _prepare_scan_crops(
                                scan_page,
                                reflow=ppt_config.fit_mode == "contain",
                                require_split=True,
                            )
                            if crops:
                                for img_bytes in crops:
                                    composer.add_slide(_apply_user_settings(img_bytes))
                                fallback_pages.discard(page_idx)
                                continue
                        # Keep an unresolved page without discarding valid question crops elsewhere.
                        add_whole_page_slide(page_idx)
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

                    slides_before_page = composer.slide_count
                    for region in regions:
                        rx0, ry0, rx1, ry1 = region.bbox
                        px0 = max(0, int(rx0 * scale_x))
                        py0 = max(0, int(ry0 * scale_y))
                        px1 = min(img_w, int(rx1 * scale_x))
                        py1 = min(img_h, int(ry1 * scale_y))
                        if px1 - px0 < 10 or py1 - py0 < 10:
                            continue
                        crop = page_img.crop((px0, py0, px1, py1))
                        crop = compact_internal_whitespace(
                            trim_bottom_whitespace(crop, padding_px=12)
                        )
                        export_img = preprocess_for_export(crop)
                        if ppt_config.fit_mode == "contain":
                            export_img = reflow_tall_question(export_img)
                        img_bytes = _image_to_bytes(export_img)
                        img_bytes = _apply_user_settings(img_bytes)
                        composer.add_slide(img_bytes)
                        del crop, export_img
                    del page_img
                    if composer.slide_count == slides_before_page:
                        fallback_pages.add(page_idx)
                        add_whole_page_slide(page_idx)

            # 텍스트 모드로 갔지만 0 슬라이드인 경우 (예: 모든 페이지가 표지/목차로 분류된 short PDF):
            # 페이지 단위 fallback로 한 번 더 시도해야 사용자가 빈 결과를 보지 않음.
            fallback_triggered = False
            if composer.slide_count == 0 and not use_whole_page:
                logger.info("PDF question-splitting yielded 0 slides - falling back to whole-page mode")
                fallback_triggered = True
                for page_idx in range(page_count):
                    if on_progress:
                        on_progress(50 + int(page_idx / max(page_count, 1) * 50), f"페이지 단위 변환 {page_idx + 1}/{page_count}")
                    add_whole_page_slide(page_idx)

        if composer.slide_count == 0:
            raise ValueError("No slides could be generated from the PDF")

        if on_progress:
            on_progress(100, "완료")

        pptx_bytes = composer.finalize()
        result_mode = "page" if (use_whole_page or fallback_triggered or fallback_pages) else "question"
        return PptResult(
            pptx_bytes=pptx_bytes,
            slide_count=composer.slide_count,
            mode=result_mode,
            narrow_slide_numbers=composer.narrow_slide_numbers,
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

        is_blank_page = False
        if not splitter_blocks:
            try:
                is_blank_page = doc.is_blank_page(page_idx)
            except Exception:  # Unverified textless pages must remain protected.
                logger.warning("PPT_PDF_BLANK_CHECK_FAILED page=%d", page_idx)

        phase1.append({
            "page_index": page_idx,
            "text_blocks": splitter_blocks,
            "page_width": page_w,
            "page_height": page_h,
            "paper_type_result": paper_type_result,
            "is_skip_page": is_skip_page,
            "is_blank_page": is_blank_page,
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
    # Record sparse coverage; unresolved pages are preserved individually below.
    low_anchor_coverage = eligible_pages >= 3 and eligible_with_anchors * 2 < eligible_pages
    if low_anchor_coverage:
        logger.warning(
            "PPT_PDF_LOW_ANCHOR_COVERAGE pages=%d eligible=%d anchored=%d; using per-page fallback",
            page_count,
            eligible_pages,
            eligible_with_anchors,
        )
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

    page_maxima = [max(r.number for r in regions) for regions in first_pass_regions if regions]
    continuously_numbered_exam = (
        len(page_maxima) >= 5
        and page_maxima[0] <= 3
        and all(0 < current - previous <= 4 for previous, current in zip(page_maxima, page_maxima[1:]))
    )
    # School exams may have marginal-looking folios on later pages. Their main
    # question numbers still advance page by page; workbook mode would instead
    # promote an embedded "1./2." experiment procedure over questions 12/13.
    workbook_doc = (signal_a and not continuously_numbered_exam) or signal_b
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
    question_slides = sum(len(regions) for regions in validated_regions)
    first_question_page = next(
        (idx for idx, regions in enumerate(validated_regions) if regions), page_count
    )
    fallback_page_indices = [
        idx for idx, page in enumerate(phase1)
        if not page["is_blank_page"]
        and not page["is_skip_page"]
        and not validated_regions[idx]
        and (
            low_anchor_coverage
            or not page["text_blocks"]
            or bool(first_pass_regions[idx])
            or idx > first_question_page
        )
    ]
    logger.info(
        "PPT_PDF_QUESTION_PLAN pages=%d eligible=%d anchors=%d "
        "marginal_pages=%d workbook=%s slides=%d fallback_pages=%d",
        page_count,
        eligible_pages,
        eligible_with_anchors,
        pages_with_marginal,
        workbook_doc,
        question_slides,
        len(fallback_page_indices),
    )
    return _PdfQuestionPlan(
        use_whole_page=question_slides == 0,
        regions_per_page=validated_regions,
        workbook_doc=workbook_doc,
        allow_image_segmentation=False,
        fallback_page_indices=fallback_page_indices,
        scan_page_indices=[
            idx for idx in fallback_page_indices if not phase1[idx]["text_blocks"]
        ],
    )


@contextmanager
def _load_pdf_segmentation(
    pdf_path: str,
    expected_page_count: Optional[int],
    page_indices: Optional[List[int]] = None,
) -> Iterator[dict[int, dict[str, Any]]]:
    """Validate page identity before consuming any detector output."""
    from academy.adapters.ai.detection.segment_dispatcher import (
        cleanup_pdf_seg_tmp_dirs,
        segment_questions_multipage,
    )

    result = None
    pages_by_index = {}
    try:
        try:
            result = segment_questions_multipage(pdf_path)
            pages = list(result.get("pages") or [])
            if expected_page_count is not None and (
                len(pages) != expected_page_count
                or [page.get("page_index") for page in pages] != list(range(expected_page_count))
            ):
                logger.warning(
                    "PPT_PDF_SEGMENTATION_PAGE_MISMATCH expected=%d actual=%d",
                    expected_page_count, len(pages),
                )
            else:
                indices = range(len(pages)) if page_indices is None else page_indices
                pages_by_index = {
                    idx: pages[idx] for idx in indices
                    if pages[idx].get("image_path") and os.path.isfile(pages[idx]["image_path"])
                }
                if len(pages_by_index) != len(indices):
                    logger.warning("PPT_PDF_SEGMENTATION_IMAGE_MISSING pages=%d", len(indices))
                    if page_indices is None:
                        pages_by_index = {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("PPT_PDF_IMAGE_SEGMENTATION_FALLBACK_ERROR path=%s error=%s", pdf_path, exc)
        yield pages_by_index
    finally:
        if result is not None:
            cleanup_pdf_seg_tmp_dirs(list(result.get("tmp_dirs") or []))


def _prepare_scan_crops(
    page: dict[str, Any],
    *,
    reflow: bool = False,
    require_split: bool = False,
) -> list[bytes]:
    """Prepare an entire scanned page before appending any of its crops."""
    from academy.domain.tools.image_preprocessor import (
        compact_internal_whitespace,
        preprocess_for_export,
        reflow_tall_question,
        trim_bottom_whitespace,
    )

    if page.get("is_skip_page") or page.get("has_embedded_text"):
        return []

    try:
        boxes = list(page.get("boxes") or [])
        # In mixed documents a lone region can be an unnumbered figure whose
        # number was inferred from neighboring pages.
        if not boxes or (require_split and len(boxes) < 2):
            return []
        with PILImage.open(page["image_path"]) as source, source.convert("RGB") as image:
            width, height = image.size
            bounds_list = []
            for x, y, w, h in boxes:
                bounds = (
                    max(0, int(x)), max(0, int(y)),
                    min(width, int(x + w)), min(height, int(y + h)),
                )
                if bounds[2] - bounds[0] < 10 or bounds[3] - bounds[1] < 10:
                    return []
                if bounds == (0, 0, width, height):
                    return []
                bounds_list.append(bounds)
            # An apparent question number may be inferred from neighboring
            # pages, so it cannot prove that a crop contains the original ink.
            if (
                not _scan_covers_page_body(image, bounds_list)
                or _scan_cuts_through_column_seam(image, bounds_list)
                or _scan_cuts_through_rows(image, bounds_list)
            ):
                return []
            crops = []
            for bounds in bounds_list:
                with image.crop(bounds) as crop:
                    export = preprocess_for_export(
                        compact_internal_whitespace(trim_bottom_whitespace(crop, padding_px=12))
                    )
                    if reflow:
                        export = reflow_tall_question(export)
                    crops.append(_image_to_bytes(export))
            return crops
    except (OSError, TypeError, ValueError):
        logger.warning("PPT_PDF_SCAN_CROP_FAILED page=%s", page.get("page_index"))
        return []


def _scan_covers_page_body(
    image: PILImage.Image,
    bounds_list: list[tuple[int, int, int, int]],
) -> bool:
    """Reject crops that collectively omit substantial body content."""
    width, height = image.size
    top, bottom = round(height * 0.09), round(height * 0.92)
    # Ignore the running header and folio when measuring printed body ink.
    with image.crop((0, top, width, bottom)).convert("L") as body:
        total_ink = sum(body.histogram()[:150])
        if not total_ink:
            return False
        with PILImage.new("L", body.size) as covered:
            draw = PILImageDraw.Draw(covered)
            for x0, y0, x1, y1 in bounds_list:
                box_top, box_bottom = max(top, y0), min(bottom, y1)
                if box_top < box_bottom:
                    draw.rectangle((x0, box_top - top, x1 - 1, box_bottom - top - 1), fill=255)
            covered_ink = sum(body.histogram(mask=covered)[:150])
            if covered_ink * 100 < total_ink * 85:
                return False
            # A single omitted answer line can account for very little total
            # ink. Check for a sustained band of uncovered print separately.
            body.paste(255, (0, 0, width, body.height), covered)
            with body.point([255] * 150 + [0] * 106) as ink, ink.resize(
                (1, body.height), PILImage.Resampling.BOX,
            ) as rows:
                run = 0
                min_band = max(3, round(height * 0.003))
                for density in rows.tobytes():
                    run = run + 1 if density >= 5 else 0
                    if run >= min_band:
                        return False
    return True


def _scan_cuts_through_column_seam(
    image: PILImage.Image,
    bounds_list: list[tuple[int, int, int, int]],
) -> bool:
    """Reject a layout split where adjacent crops divide printed ink."""
    probe = max(2, round(image.width * 0.004))
    for x0, y0, x1, y1 in bounds_list:
        if not image.width // 3 <= x1 <= 2 * image.width // 3:
            continue
        for next_x0, next_y0, next_x1, next_y1 in bounds_list:
            top, bottom = max(y0, next_y0), min(y1, next_y1)
            if abs(next_x0 - x1) > probe or bottom - top < 10:
                continue
            if x1 - x0 < probe or next_x1 - x1 < probe:
                continue
            with image.crop((x1 - probe, top, x1 + probe, bottom)) as strip:
                # A centered divider may be inked, but a true gutter is clear
                # immediately to one or both sides of that divider.
                ink = strip.convert("L").point(lambda pixel: 255 if pixel < 150 else 0)
                left = ink.crop((0, 0, probe, ink.height)).resize((1, ink.height), PILImage.Resampling.BOX)
                right = ink.crop((probe, 0, 2 * probe, ink.height)).resize((1, ink.height), PILImage.Resampling.BOX)
                if sum(a >= 64 and b >= 64 for a, b in zip(left.tobytes(), right.tobytes())) >= max(
                    4, round(0.012 * (bottom - top)),
                ):
                    return True
    return False


def _scan_cuts_through_rows(
    image: PILImage.Image,
    bounds_list: list[tuple[int, int, int, int]],
) -> bool:
    """Detect ink crossing a crop's top or bottom, even when the page is covered."""
    probe = max(2, round(image.height * 0.002))
    ink_threshold = [255] * 150 + [0] * 106
    for x0, y0, x1, y1 in bounds_list:
        width = x1 - x0
        min_ink_columns = max(5, round(width * 0.012))
        for edge in (y0, y1):
            if edge < probe or edge + probe > image.height:
                continue
            with image.crop((x0, edge - probe, x1, edge + probe)) as strip:
                ink = strip.convert("L").point(ink_threshold)
                above = ink.crop((0, 0, width, probe)).resize((width, 1), PILImage.Resampling.BOX)
                below = ink.crop((0, probe, width, 2 * probe)).resize((width, 1), PILImage.Resampling.BOX)
                if sum(a >= 64 and b >= 64 for a, b in zip(above.tobytes(), below.tobytes())) >= min_ink_columns:
                    return True
    return False


def _add_segmented_pdf_slides_to_composer(
    pdf_path: str,
    *,
    composer: Any,
    apply_user_settings: Callable[[bytes], bytes],
    on_progress: Optional[Callable[[int, str], None]] = None,
    expected_page_count: Optional[int] = None,
    fallback_pages: Optional[set[int]] = None,
) -> int:
    """Use the Matchup image segmentation path when a PDF has no text layer."""
    initial_slide_count = composer.slide_count
    try:
        with _load_pdf_segmentation(pdf_path, expected_page_count) as pages:
            return _append_segmented_pdf_pages(
                pages, composer, apply_user_settings, on_progress, fallback_pages,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "PPT_PDF_IMAGE_SEGMENTATION_FALLBACK_ERROR path=%s error=%s",
            pdf_path,
            exc,
        )
        if composer.slide_count != initial_slide_count:
            raise
        return 0


def _append_segmented_pdf_pages(
    pages: dict[int, dict[str, Any]],
    composer: Any,
    apply_user_settings: Callable[[bytes], bytes],
    on_progress: Optional[Callable[[int, str], None]],
    fallback_pages: Optional[set[int]],
) -> int:
    from academy.domain.tools.image_preprocessor import preprocess_for_export

    total_pages = len(pages)
    added = 0
    for page_idx, page in pages.items():
        if on_progress:
            pct = int(page_idx / max(total_pages, 1) * 100)
            on_progress(pct, f"문항 슬라이드 {page_idx + 1}/{total_pages}")
        crops = _prepare_scan_crops(page)
        if not crops:
            with PILImage.open(page["image_path"]) as source_img, source_img.convert("RGB") as page_img:
                page_img.thumbnail((2400, 2400), resample=PILImage.LANCZOS)
                export_img = preprocess_for_export(page_img)
                crops = [_image_to_bytes(export_img, fmt="JPEG")]
            if fallback_pages is not None:
                fallback_pages.add(page_idx)
        for img_bytes in crops:
            composer.add_slide(apply_user_settings(img_bytes))
        added += len(crops)
    return added


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
