"""Large physical PDF pages keep Matchup's image and region coordinates aligned."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

from academy.adapters.ai.detection import segment_dispatcher as dispatcher
from academy.adapters.tools import pymupdf_renderer
from academy.application.use_cases.ai.pipelines.matchup_pipeline import _boxes_to_questions
from academy.domain.tools import paper_type, question_splitter


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_pdf_rotation_uses_rendered_coordinates_for_question_crops(tmp_path, rotation):
    import fitz

    source = tmp_path / "rotated-exam.pdf"
    width, height = 595, 842
    media_width, media_height = (height, width) if rotation in (90, 270) else (width, height)
    starts = [85, 260, 435]
    with fitz.open() as pdf:
        page = pdf.new_page(width=media_width, height=media_height)
        page.set_rotation(rotation)
        for number, y in enumerate(starts, 1):
            for x, row, text in (
                (35, y, f"{number}. Find the value of x in this equation."),
                (50, y + 34, "Given that x + 3 = 8, choose the correct option."),
                (50, y + 62, "A. 3    B. 5    C. 8    D. 11"),
            ):
                position = fitz.Point(x, row) * page.derotation_matrix
                page.insert_text(position, text, fontsize=12, rotate=rotation)
        pdf.save(source)

    with pymupdf_renderer.PdfDocument(str(source)) as pdf:
        assert pdf.page_dimensions(0) == (width, height)
        starts_by_word = {
            word.text: word for word in pdf.extract_text_words(0)
            if word.text in {"1.", "2.", "3."}
        }
        assert [starts_by_word[f"{n}."].x0 for n in range(1, 4)] == pytest.approx([35] * 3)

    dispatcher.begin_pdf_seg_scope()
    try:
        result = dispatcher.segment_questions_multipage(str(source), source_type="school_exam_pdf")
        page = result["pages"][0]
        assert page["numbers"] == [1, 2, 3]
        with Image.open(page["image_path"]) as rendered:
            scale = rendered.height / height
            previous_bottom = 0
            for row, (x, y, w, h) in zip(starts, page["boxes"]):
                assert previous_bottom <= y < round(row * scale) < y + h
                assert round((row + 62) * scale) < y + h
                with rendered.crop((x, y, x + w, y + h)) as crop:
                    assert ImageChops.invert(crop).getbbox() is not None
                previous_bottom = y + h
    finally:
        dispatcher.cleanup_registered_pdf_seg_tmp_dirs()


def test_school_exam_with_marginal_labels_keeps_bottom_answer_choices(tmp_path):
    import fitz

    source = tmp_path / "school-exam-with-figure-labels.pdf"
    choice_y = 820
    with fitz.open() as pdf:
        for page_index in range(5):
            page = pdf.new_page(width=595, height=842)
            for column, x in enumerate((34, 309)):
                number = page_index * 2 + column + 1
                page.insert_text((x, 60), f"{number}. Which statement is correct?", fontsize=12)
                page.insert_text((x + 15, 100), "Use the information in the figure.", fontsize=11)
                page.draw_rect(fitz.Rect(x + 14, 140, x + 208, 250), color=(0, 0, 0), width=1)
                page.insert_text((x + 10, 350), f"{number}.", fontsize=12)
                page.insert_text((x + 15, 760), "A. Experiment One   B. Experiment Two", fontsize=10)
                page.insert_text((x + 2, choice_y), "(1) A    (2) B    (3) Both    (4) Neither", fontsize=10)
        pdf.save(source)

    dispatcher.begin_pdf_seg_scope()
    try:
        result = dispatcher.segment_questions_multipage(str(source), source_type="school_exam_pdf")
        assert [page["numbers"] for page in result["pages"]] == [[n, n + 1] for n in range(1, 10, 2)]
        for page in result["pages"]:
            with Image.open(page["image_path"]) as rendered:
                scale = rendered.height / 842
                for x, y, w, h in page["boxes"]:
                    assert y < round(60 * scale) < round(choice_y * scale) < y + h
    finally:
        dispatcher.cleanup_registered_pdf_seg_tmp_dirs()


@pytest.mark.parametrize(
    ("width", "height", "page_count"),
    [(595, 842, 1), (3000, 4000, 9), (4284, 5712, 1), (72, 14400, 1)],
)
def test_pdf_page_budget_preserves_result_geometry_and_metadata(
    monkeypatch, tmp_path, width, height, page_count,
):
    rendered = []

    class FakeImage:
        def __init__(self, dpi):
            zoom = min(dpi / 72, 8192 / max(width, height))
            self.size = (round(width * zoom), round(height * zoom))
            self.closed = False

        def save(self, path, _format):
            Path(path).write_bytes(b"synthetic page")

        def close(self):
            self.closed = True

    class FakeDocument:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def page_count(self):
            return page_count

        def page_dimensions(self, _index):
            return float(width), float(height)

        def render_page(self, index, dpi):
            image = FakeImage(dpi)
            rendered.append((index, dpi, image))
            return image

        def extract_text_blocks(self, _index):
            return [pymupdf_renderer.TextBlock("Question", 0, 0, 100, 50)]

        def extract_text_words(self, _index):
            return []

    def split_questions(_blocks, page_width, page_height, *, page_index, **_kwargs):
        return [question_splitter.QuestionRegion(
            number=page_index + 1,
            bbox=(page_width * .1, page_height * .1, page_width * .4, page_height * .3),
            body_bbox=(page_width * .12, page_height * .12, page_width * .38, page_height * .28),
            context_bbox=(page_width * .08, page_height * .08, page_width * .42, page_height * .32),
            page_index=page_index,
            semantic_flags=("synthetic_question",),
        )]

    monkeypatch.setattr(pymupdf_renderer, "PdfDocument", FakeDocument)
    monkeypatch.setattr(paper_type, "classify_paper_type", lambda **_kwargs: SimpleNamespace(
        paper_type=paper_type.PaperType.UNKNOWN, debug={}, is_non_question=False,
    ))
    monkeypatch.setattr(question_splitter, "count_marginal_anchor_candidates", lambda *_args: 0)
    monkeypatch.setattr(question_splitter, "split_questions", split_questions)
    monkeypatch.setattr(question_splitter, "validate_anchors_across_pages", lambda pages: pages)
    monkeypatch.setattr(dispatcher, "_looks_like_sparse_problem_text_overlay", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(dispatcher, "_looks_like_partial_text_overlay_scan_page", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(dispatcher, "_should_expand_text_regions_by_visual_x", lambda *_args: False)
    monkeypatch.setattr(dispatcher, "_trim_other_source_text_regions_to_ink", lambda *_args, **_kwargs: None)

    dispatcher.begin_pdf_seg_scope()
    result = dispatcher.segment_questions_multipage(str(tmp_path / "synthetic.pdf"))
    try:
        assert result["is_pdf"] is True
        assert result["total_boxes"] == page_count
        assert len(result["pages"]) == page_count
        questions = _boxes_to_questions(result["pages"])
        assert len(questions) == page_count
        assert [item[0] for item in rendered] == list(range(page_count))
        assert all(image.closed for _, _, image in rendered)
        for index, dpi, image in rendered:
            assert image.size[0] * image.size[1] <= dispatcher._PDF_RENDER_MAX_PIXELS
            if width in (595, 72):
                assert dpi == 200.0
            else:
                assert dpi < 200.0
            page = result["pages"][index]
            assert page["page_index"] == index
            assert page["numbers"] == [index + 1]
            assert Path(page["image_path"]).exists()
            sx = dispatcher._PDF_TO_PIXEL_SCALE if width == 595 else image.size[0] / width
            sy = dispatcher._PDF_TO_PIXEL_SCALE if width == 595 else image.size[1] / height
            expected = dispatcher._bbox_points_to_pixels(
                (width * .1, height * .1, width * .4, height * .3),
                scale_x=sx, scale_y=sy,
            )
            assert page["boxes"] == [expected]
            meta = page["bbox_meta"][0]
            assert meta["version"] == "question_region_v2"
            assert meta["display_box"] == meta["audit_box"] == expected
            assert meta["body_box"] != expected
            assert meta["context_box"] != expected
            # Very narrow bounded rasters quantize coordinates in whole pixels.
            assert abs(expected[0] / image.size[0] - .1) <= max(.002, 1 / image.size[0])
            assert abs(expected[1] / image.size[1] - .1) <= max(.002, 1 / image.size[1])
            assert questions[index]["page_index"] == index
            assert questions[index]["bbox"] == list(expected)
            assert questions[index]["meta_extra"]["segmentation_flags"] == ["synthetic_question"]
    finally:
        dispatcher.cleanup_registered_pdf_seg_tmp_dirs()
    assert not Path(result["tmp_dirs"][0]).exists()


def test_unexpected_render_size_fails_and_closes_image(monkeypatch, tmp_path):
    image = SimpleNamespace(
        size=(dispatcher._PDF_RENDER_MAX_PIXELS + 1, 1),
        closed=False,
    )
    image.close = lambda: setattr(image, "closed", True)
    image.save = lambda *_args: pytest.fail("oversized image must not be saved")

    class FakeDocument:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def page_count(self):
            return 1

        def page_dimensions(self, _index):
            return 3000.0, 4000.0

        def render_page(self, _index, dpi):
            assert dpi < 200.0
            return image

    original_mkdtemp = dispatcher.tempfile.mkdtemp
    monkeypatch.setattr(
        dispatcher.tempfile, "mkdtemp",
        lambda **kwargs: original_mkdtemp(dir=tmp_path, **kwargs),
    )
    monkeypatch.setattr(pymupdf_renderer, "PdfDocument", FakeDocument)
    dispatcher.begin_pdf_seg_scope()
    try:
        with pytest.raises(ValueError, match="pixel budget"):
            dispatcher._pdf_to_images("synthetic.pdf")
    finally:
        dispatcher.cleanup_registered_pdf_seg_tmp_dirs()
    assert image.closed
    assert not list(tmp_path.glob("pdf-seg-*"))


@pytest.mark.parametrize(("width", "height"), [(595, 842), (3000, 4000), (4284, 5712)])
def test_real_pdf_render_keeps_content_under_budget(tmp_path, width, height):
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "oversized.pdf"
    with fitz.open() as document:
        page = document.new_page(width=width, height=height)
        page.insert_text((width * .1, height * .1), "Q1 12345", fontsize=18)
        document.save(pdf_path)

    with pymupdf_renderer.PdfDocument(str(pdf_path)) as document:
        dpi = dispatcher._pdf_render_dpi(*document.page_dimensions(0))
        image = document.render_page(0, dpi=dpi)
    try:
        if width == 595:
            assert dpi == 200.0
        else:
            assert dpi < 200.0
        assert image.width * image.height <= dispatcher._PDF_RENDER_MAX_PIXELS
        sx, sy = image.width / width, image.height / height
        x, y = int(width * .1 * sx), int(height * .1 * sy)
        label = image.crop((x, y - math.ceil(24 * sy), x + math.ceil(110 * sx), y + 2))
        try:
            assert min(channel[0] for channel in label.getextrema()) < 100
        finally:
            label.close()
    finally:
        image.close()
