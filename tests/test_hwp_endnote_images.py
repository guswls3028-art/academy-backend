import struct
import zlib
from xml.etree import ElementTree
from zipfile import ZipFile

from io import BytesIO
from PIL import Image

from academy.adapters.tools.hwp_endnote_images import (
    HwpEndnoteExtraction,
    HwpEndnoteVisual,
    _collect_endnote_picture_ids,
    _collect_endnote_contents,
    _collect_endnote_numbers,
    _collect_hwp_question_contents,
    _decode_hwp_equation,
    _hwp_equation_to_mathtext,
    _hwpx_body_content,
    _humanize_hwp_equation,
    _load_picture,
    _render_equation_image,
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
    assert _humanize_hwp_equation("0LEQx LEQ2pi, ita, it-3a") == (
        "0≤x≤2π, a, -3a"
    )
    assert _humanize_hwp_equation("0letheta<2pi") == "0≤θ<2π"
    assert _humanize_hwp_equation("molecule") == "molecule"


def test_strips_hangul_eqedit_internal_text_object_sentinel():
    script = "rmQ\n\nTo\n20002"
    payload = (
        b"\x00\x00\x00\x00"
        + struct.pack("<H", len(script))
        + script.encode("utf-16le")
    )

    assert _decode_hwp_equation(payload) == "rmQ"


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


def test_collects_clean_hwp_body_after_endnote_without_solution_records():
    equation_control = (
        struct.pack("<H", 0x0B)
        + b"deqe"
        + (b"\x00\x00" * 4)
        + struct.pack("<H", 0x0B)
    )
    script = "cos theta=1"
    equation = (
        b"\x00\x00\x00\x00"
        + struct.pack("<H", len(script))
        + script.encode("utf-16le")
    )
    solution_picture = bytearray(73)
    struct.pack_into("<H", solution_picture, 71, 8)
    problem_picture = bytearray(73)
    struct.pack_into("<H", problem_picture, 71, 9)
    records = [
        (71, 1, b"  ne" + struct.pack("<I", 3)),
        (67, 3, "손필기 해설".encode("utf-16le")),
        (85, 5, bytes(solution_picture)),
        (67, 1, "깨끗한 문제 ".encode("utf-16le") + equation_control),
        (88, 2, equation),
        (85, 3, bytes(problem_picture)),
        (71, 1, b"  ne" + struct.pack("<I", 4)),
        (67, 3, "다음 해설".encode("utf-16le")),
        (67, 1, "다음 문제".encode("utf-16le")),
        (67, 1, "정답 및 해설".encode("utf-16le")),
    ]

    contents = _collect_hwp_question_contents(records)

    assert [item.number for item in contents] == [3, 4]
    assert "깨끗한 문제" in contents[0].paragraphs[0]
    assert script in contents[0].paragraphs[0]
    assert "손필기 해설" not in " ".join(contents[0].paragraphs)
    assert contents[0].picture_refs == (9,)
    assert contents[1].paragraphs == ("다음 문제",)


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
    glued = _hwp_equation_to_mathtext("costheta _{1}+sinx")
    assert r"\cos \theta" in glued
    assert r"\sin x" in glued
    compact_source = _hwp_equation_to_mathtext("rm{PQ")
    assert compact_source == r"\mathrm{PQ}"
    assert _hwp_equation_to_mathtext("5 over 3") == r"\frac{5}{3}"
    assert _hwp_equation_to_mathtext("0letheta<2pi") == (
        r"0\leq \theta<2\pi"
    )
    assert _hwp_equation_to_mathtext("molecule") == "molecule"


def test_equation_render_falls_back_when_mathtext_is_unavailable(monkeypatch):
    original_import = __import__

    def import_without_matplotlib(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ModuleNotFoundError("matplotlib is unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_without_matplotlib)

    image = _render_equation_image("x over 2")
    try:
        assert image.width > 0
        assert image.height > 0
    finally:
        image.close()


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


def test_hwpx_reconstructs_problem_from_body_not_endnote_image(tmp_path):
    solution_bytes = BytesIO()
    Image.new("RGB", (320, 180), "red").save(solution_bytes, format="PNG")
    problem_bytes = BytesIO()
    Image.new("RGB", (120, 90), "blue").save(problem_bytes, format="PNG")
    source = tmp_path / "combined.hwpx"
    with ZipFile(source, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr(
            "Contents/section0.xml",
            (
                '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
                '<hp:p><hp:run><hp:ctrl><hp:endNote number="1">'
                '<hp:subList><hp:p><hp:run><hp:pic>'
                '<hp:img binaryItemIDRef="solution1"/>'
                '</hp:pic></hp:run></hp:p></hp:subList>'
                '</hp:endNote></hp:ctrl><hp:t>깨끗한 본문 문제</hp:t>'
                '<hp:equation><hp:script>x over 2</hp:script></hp:equation>'
                '<hp:pic><hp:img binaryItemIDRef="problem1"/></hp:pic>'
                '</hp:run></hp:p></hs:sec>'
            ),
        )
        archive.writestr("BinData/solution1.png", solution_bytes.getvalue())
        archive.writestr("BinData/problem1.png", problem_bytes.getvalue())

    extraction = extract_hwpx_endnotes(
        str(source),
        include_problem_reconstruction=True,
    )

    assert extraction.missing_visual_numbers == ()
    assert extraction.missing_problem_visual_numbers == ()
    assert len(extraction.problem_visuals) == 1
    assert extraction.problem_visuals[0].render_mode == "source_body_reconstruction"
    assert extraction.problem_visuals[0].picture_count == 1


def test_hwpx_keeps_text_after_tabs_inside_choice_paragraphs():
    paragraph = ElementTree.fromstring(
        '<hp:p xmlns:hp="urn:paragraph"><hp:run><hp:t>'
        '① <hp:tab/>② <hp:tab/>③'
        '</hp:t></hp:run></hp:p>'
    )

    paragraphs, image_refs = _hwpx_body_content(paragraph)

    assert paragraphs == ("① \u2003\u2003② \u2003\u2003③",)
    assert image_refs == ()


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


def test_single_hwp_requires_complete_clean_body_problem_coverage(monkeypatch):
    explanation = HwpEndnoteVisual(
        number=1,
        png_bytes=b"solution",
        width=100,
        height=200,
        picture_count=1,
    )
    monkeypatch.setattr(
        "academy.application.use_cases.ai.pipelines.hwp_question_pipeline."
        "extract_document_endnotes",
        lambda _path, _filename, **_kwargs: HwpEndnoteExtraction(
            control_numbers=(1,),
            visuals=(explanation,),
        ),
    )
    job = AIJob.new(
        type="question_segmentation",
        payload={"exam_id": "7", "filename": "combined.hwp"},
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
    assert result.result["missing_problem_numbers"] == [1]


def test_single_hwp_uploads_clean_body_problem_instead_of_cropping_solution(monkeypatch):
    explanation = HwpEndnoteVisual(
        number=1,
        png_bytes=b"solution",
        width=100,
        height=200,
        picture_count=1,
    )
    problem = HwpEndnoteVisual(
        number=1,
        png_bytes=b"clean-problem",
        width=1600,
        height=900,
        picture_count=0,
        render_mode="source_body_reconstruction",
    )
    monkeypatch.setattr(
        "academy.application.use_cases.ai.pipelines.hwp_question_pipeline."
        "extract_document_endnotes",
        lambda _path, _filename, **_kwargs: HwpEndnoteExtraction(
            control_numbers=(1,),
            visuals=(explanation,),
            problem_visuals=(problem,),
        ),
    )
    uploads = []
    monkeypatch.setattr(
        "academy.application.use_cases.ai.pipelines.hwp_question_pipeline."
        "upload_fileobj_to_r2_storage",
        lambda **kwargs: uploads.append(
            {**kwargs, "bytes": kwargs["fileobj"].getvalue()}
        ),
    )
    job = AIJob.new(
        type="question_segmentation",
        payload={"exam_id": "7", "filename": "combined.hwp"},
        tenant_id="3",
    )

    result = run_hwp_question_pipeline(
        job=job,
        local_path="unused.hwp",
        payload=job.payload,
        tenant_id=job.tenant_id,
        record_progress=lambda *_args, **_kwargs: None,
    )

    question_upload = next(item for item in uploads if "/questions/" in item["key"])
    assert question_upload["bytes"] == b"clean-problem"
    assert result.result["questions"][0]["problem_crop_ratio"] == 1.0
    assert result.result["questions"][0]["bbox"] == [0, 0, 1600, 900]
    assert result.result["segmentation_method"] == "hwp_body_endnote"


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
