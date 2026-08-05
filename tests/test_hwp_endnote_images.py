import struct
import zlib
from zipfile import ZipFile

from io import BytesIO
from PIL import Image

from academy.adapters.tools.hwp_endnote_images import (
    HwpEndnoteExtraction,
    HwpEndnoteVisual,
    _collect_endnote_picture_ids,
    _collect_endnote_contents,
    _collect_endnote_numbers,
    _hwp_equation_to_mathtext,
    _humanize_hwp_equation,
    _load_picture,
    extract_hwpx_endnotes,
)
from apps.shared.contracts.ai_job import AIJob
from academy.application.use_cases.ai.pipelines.hwp_question_pipeline import (
    extract_and_upload_hwp_explanations,
    merge_paired_teacher_explanations,
    run_hwp_question_pipeline,
)


def test_collects_picture_ids_inside_numbered_endnotes():
    endnote = b"  ne" + struct.pack("<I", 3)
    picture_a = bytearray(73)
    struct.pack_into("<H", picture_a, 71, 10)
    picture_b = bytearray(73)
    struct.pack_into("<H", picture_b, 71, 11)
    records = [
        (71, 2, endnote),
        (85, 3, bytes(picture_a)),
        (85, 3, bytes(picture_b)),
        (71, 2, b"xxxx" + struct.pack("<I", 1)),
    ]
    assert _collect_endnote_picture_ids(records) == [(3, [10, 11])]


def test_ignores_picture_records_outside_endnotes():
    picture = bytearray(73)
    struct.pack_into("<H", picture, 71, 7)
    assert _collect_endnote_picture_ids([(85, 3, bytes(picture))]) == []


def test_collects_all_endnote_numbers_even_without_pictures():
    records = [
        (71, 2, b"  ne" + struct.pack("<I", 1)),
        (71, 2, b"  ne" + struct.pack("<I", 2)),
    ]

    assert _collect_endnote_numbers(records) == [1, 2]


def test_collects_legacy_hwp_endnote_text_and_native_equations_in_order():
    equation_control = (
        struct.pack("<H", 0x0B)
        + b"deqe"
        + (b"\x00\x00" * 4)
        + struct.pack("<H", 0x0B)
    )
    paragraph = "따라서 ".encode("utf-16le") + equation_control + "이다.".encode("utf-16le")
    script = "{x+1} over {2}"
    equation = b"\x00\x00\x00\x00" + struct.pack("<H", len(script)) + script.encode("utf-16le")
    records = [
        (71, 1, b"  ne" + struct.pack("<I", 7)),
        (67, 3, paragraph),
        (88, 4, equation),
        (71, 1, b"xxxx" + struct.pack("<I", 1)),
    ]

    contents = _collect_endnote_contents(records)

    assert len(contents) == 1
    assert contents[0].number == 7
    assert contents[0].equation_count == 1
    assert contents[0].paragraphs[0].startswith("따라서 ")
    assert "{x+1} over {2}" in contents[0].paragraphs[0]
    assert contents[0].paragraphs[0].endswith("이다.")


def test_humanizes_common_hwp_equation_tokens_without_changing_meaning():
    assert _humanize_hwp_equation(
        "lim _{x`` rarrow ``INF} {sqrt {{x+1} over {2}}}"
    ) == "lim _(x → ∞) (√((x+1)/(2)))"
    assert _humanize_hwp_equation("2fprime(1), rm3, xge1, root3") == (
        "2f′(1), 3, x≥1, √3"
    )
    assert _humanize_hwp_equation(
        "x GEQ 0, 5sqrt2, 2pile{ IT#IT }, C CUP A"
    ) == (
        "x ≥ 0, 5√2, 2, C ∪ A"
    )


def test_parent_paragraph_receives_equations_after_nested_table_paragraphs():
    equation_control = (
        struct.pack("<H", 0x0B)
        + b"deqe"
        + (b"\x00\x00" * 4)
        + struct.pack("<H", 0x0B)
    )

    def equation(script: str) -> bytes:
        return (
            b"\x00\x00\x00\x00"
            + struct.pack("<H", len(script))
            + script.encode("utf-16le")
        )

    records = [
        (71, 1, b"  ne" + struct.pack("<I", 1)),
        (67, 3, "함수 ".encode("utf-16le") + equation_control),
        (67, 5, equation_control),
        (88, 6, equation("x")),
        (88, 4, equation("f(x)")),
        (71, 1, b"xxxx" + struct.pack("<I", 1)),
    ]

    contents = _collect_endnote_contents(records)

    assert len(contents) == 1
    assert "f(x)" in contents[0].paragraphs[0]
    assert "x" in contents[0].paragraphs[1]
    assert all("[수식 확인 필요]" not in value for value in contents[0].paragraphs)


def test_hwp_equation_mathtext_handles_fractions_piecewise_and_braces():
    latex = _hwp_equation_to_mathtext(
        "f LEFT (x RIGHT )={cases{{1} over {x}&LEFT (x GEQ 1 RIGHT )#0&(x<1)}}"
    )

    assert r"\frac{1}{x}" in latex
    assert r"\left\{" in latex
    assert r"\geq" in latex

    compact = _hwp_equation_to_mathtext(
        "x GEQ 0, 5sqrt2, 2pile{ IT#IT }, C CUP A"
    )
    assert r"x \geq 0" in compact
    assert r"5\sqrt{2}" in compact
    assert "pile" not in compact
    assert r"C \cup A" in compact


def test_loads_raw_deflate_compressed_hwp_bitmap():
    source = BytesIO()
    Image.new("RGB", (32, 24), "white").save(source, format="BMP")
    compressor = zlib.compressobj(level=6, wbits=-15)
    compressed = compressor.compress(source.getvalue()) + compressor.flush()

    image = _load_picture(compressed)

    assert image is not None
    assert image.size == (32, 24)
    image.close()


def test_extracts_original_hwpx_endnote_images(tmp_path):
    image_bytes = BytesIO()
    Image.new("RGB", (320, 180), "white").save(image_bytes, format="PNG")
    source = tmp_path / "teacher.hwpx"
    with ZipFile(source, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr(
            "Contents/section0.xml",
            (
                '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
                '<hp:p><hp:run><hp:ctrl><hp:endNote number="1">'
                '<hp:subList><hp:p><hp:run><hp:pic>'
                '<hp:img binaryItemIDRef="image1"/>'
                "</hp:pic></hp:run></hp:p></hp:subList>"
                "</hp:endNote></hp:ctrl></hp:run></hp:p></hs:sec>"
            ),
        )
        archive.writestr("BinData/image1.png", image_bytes.getvalue())

    extraction = extract_hwpx_endnotes(str(source))

    assert extraction.control_numbers == (1,)
    assert extraction.missing_visual_numbers == ()
    assert len(extraction.visuals) == 1
    assert extraction.visuals[0].number == 1
    assert (extraction.visuals[0].width, extraction.visuals[0].height) == (320, 180)


def test_single_hwp_with_partial_visual_coverage_requires_problem_pdf(monkeypatch):
    visual = HwpEndnoteVisual(
        number=1,
        png_bytes=b"png",
        width=100,
        height=100,
        picture_count=1,
    )
    monkeypatch.setattr(
        "academy.application.use_cases.ai.pipelines.hwp_question_pipeline."
        "extract_document_endnotes",
        lambda _path, _filename, **_kwargs: HwpEndnoteExtraction(
            control_numbers=(1, 2),
            visuals=(visual,),
        ),
    )
    job = AIJob.new(
        type="question_segmentation",
        payload={"exam_id": "7", "filename": "problems.hwp"},
        tenant_id="3",
    )

    result = run_hwp_question_pipeline(
        job=job,
        local_path="unused.hwp",
        payload=job.payload,
        tenant_id=job.tenant_id,
        record_progress=lambda *_args, **_kwargs: None,
    )

    assert result.status == "DONE"
    assert result.result["conversion_required"] is True
    assert result.result["missing_visual_numbers"] == [2]
    assert result.result["source_mode"] == "problem_document_requires_pdf"


def test_paired_teacher_hwp_matches_clean_problem_source_numbers_only():
    result = merge_paired_teacher_explanations(
        primary_result={
            "questions": [
                {"number": 1, "original_number": 1},
                {"number": 8, "original_number": 3},
            ],
            "explanations": [{"question_number": 99, "text": "PDF 해설"}],
        },
        teacher_explanations=[
            {"question_number": 1, "image_key": "q1.png"},
            {"question_number": 3, "image_key": "q3.png"},
            {"question_number": 7, "image_key": "q7.png"},
        ],
    )

    assert [item["question_number"] for item in result["explanations"]] == [1, 3]
    assert result["teacher_explanation_count"] == 2
    assert result["unmatched_teacher_explanation_numbers"] == [7]
    assert result["explanation_source_mode"] == "paired_teacher_hwp"


def test_paired_teacher_hwp_uses_complete_reconstructed_endnote_visuals(monkeypatch):
    exact_visual = HwpEndnoteVisual(
        number=1,
        png_bytes=b"exact",
        width=100,
        height=100,
        picture_count=1,
    )
    reconstructed = tuple(
        HwpEndnoteVisual(
            number=number,
            png_bytes=f"reconstructed-{number}".encode(),
            width=1600,
            height=900,
            picture_count=1 if number == 1 else 0,
            render_mode="source_content_reconstruction",
        )
        for number in (1, 2)
    )
    monkeypatch.setattr(
        "academy.application.use_cases.ai.pipelines.hwp_question_pipeline."
        "extract_document_endnotes",
        lambda _path, _filename, **_kwargs: HwpEndnoteExtraction(
            control_numbers=(1, 2),
            visuals=(exact_visual,),
            paired_visuals=reconstructed,
        ),
    )
    uploads = []
    monkeypatch.setattr(
        "academy.application.use_cases.ai.pipelines.hwp_question_pipeline."
        "upload_fileobj_to_r2_storage",
        lambda **kwargs: uploads.append(kwargs),
    )

    extraction, explanations = extract_and_upload_hwp_explanations(
        local_path="teacher.hwp",
        filename="teacher.hwp",
        tenant_id="3",
        exam_id=8,
    )

    assert extraction.missing_visual_numbers == (2,)
    assert extraction.missing_paired_visual_numbers == ()
    assert [item["question_number"] for item in explanations] == [1, 2]
    assert {item["source_render_mode"] for item in explanations} == {
        "source_content_reconstruction"
    }
    assert len(uploads) == 2
