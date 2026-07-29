from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from apps.domains.tools.problem_studio.extractors import (
    _HWP_ENDNOTE_ANCHOR,
    _decode_hwp_para_text,
    _rebuild_hwp_question_blocks,
    extract_hwpx_document_profile,
    extract_hwpx_question_images,
    extract_hwpx_text,
)
from apps.domains.tools.problem_studio.hwpx_writer import build_hwpx_exam_document
from apps.domains.tools.problem_studio.structure import structure_text
from apps.domains.tools.problem_studio.transfer_documents import build_transfer_package
from apps.domains.tools.problem_studio.voice_profiles import (
    augment_voice_profile_with_source_items,
)


def _hwpx_with_text(
    *,
    preview: str,
    section: str,
    header: str = "",
    extra_members: dict[str, bytes] | None = None,
) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("Preview/PrvText.txt", preview)
        archive.writestr("Contents/section0.xml", section)
        if header:
            archive.writestr("Contents/header.xml", header)
        for name, data in (extra_members or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def test_hwpx_prefers_complete_section_text_over_truncated_preview():
    data = _hwpx_with_text(
        preview="미리보기 1,023자까지만 존재",
        section=(
            '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
            '<hp:p><hp:run><hp:t>첫 문항</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run><hp:t>미리보기에 없는 마지막 해설</hp:t></hp:run></hp:p>'
            "</hs:sec>"
        ),
    )

    extracted = extract_hwpx_text(data)

    assert extracted == "첫 문항\n미리보기에 없는 마지막 해설"
    assert "미리보기 1,023자까지만 존재" not in extracted


def test_hwpx_extracts_nested_paragraph_once_and_keeps_equation_script():
    data = _hwpx_with_text(
        preview="표 안의 문장\n물 분자 {rm H _ {2} O}",
        section=(
            '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
            "<hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList>"
            "<hp:p><hp:run><hp:t>표 안의 문장</hp:t></hp:run></hp:p>"
            "</hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>"
            "<hp:p><hp:run><hp:t>물 분자</hp:t>"
            "<hp:equation><hp:script>{rm H _ {2} O}</hp:script></hp:equation>"
            "</hp:run></hp:p>"
            "</hs:sec>"
        ),
    )

    extracted = extract_hwpx_text(data)

    assert extracted.count("표 안의 문장") == 1
    assert "물 분자 [[수식:{rm H _ {2} O}]]" in extracted


def test_native_hwpx_equation_round_trips_as_editable_equation_not_raw_script():
    source = _hwpx_with_text(
        preview="",
        section=(
            '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
            "<hp:p><hp:run><hp:t>물 분자는 </hp:t>"
            "<hp:equation><hp:script>{rm H _ {2} O}</hp:script></hp:equation>"
            "<hp:t>이다.</hp:t></hp:run></hp:p>"
            "</hs:sec>"
        ),
    )
    extracted = extract_hwpx_text(source)
    generated = build_hwpx_exam_document(
        title="수식 왕복",
        meta_lines=[],
        items=[{
            "number": 1,
            "prompt": extracted,
            "choices": [],
            "answer": "",
            "explanation": "",
        }],
    )

    with ZipFile(BytesIO(generated)) as archive:
        section = archive.read("Contents/section0.xml").decode("utf-8")

    assert "<hp:equation" in section
    assert "<hp:script>{rm H _ {2} O}</hp:script>" in section
    assert "[[수식:" not in section


def test_hwp_para_text_removes_inline_control_payload_and_preserves_hanja():
    inline_control = "\x0beq\x00\x00\x00\x00\x0b"
    payload = f"산화{inline_control}還元\r다음".encode("utf-16le")

    assert _decode_hwp_para_text(payload) == "산화還元\n다음"


def test_hwpx_endnotes_rebuild_numbered_problem_and_explanation_blocks():
    data = _hwpx_with_text(
        preview="첫 문제만 있는 잘린 미리보기",
        section=(
            '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
            "<hp:p><hp:run><hp:t>산성 물질을 고르시오.</hp:t>"
            '<hp:ctrl><hp:endNote number="1"><hp:subList>'
            "<hp:p><hp:run><hp:t>정답 ②</hp:t></hp:run></hp:p>"
            "<hp:p><hp:run><hp:t>식초는 산성이다.</hp:t></hp:run></hp:p>"
            "</hp:subList></hp:endNote></hp:ctrl></hp:run></hp:p>"
            "<hp:p><hp:run><hp:t>① 비눗물 ② 식초</hp:t></hp:run></hp:p>"
            "<hp:p><hp:run><hp:t>염기성 물질을 고르시오.</hp:t>"
            '<hp:ctrl><hp:endNote number="2"><hp:subList>'
            "<hp:p><hp:run><hp:t>정답 ①</hp:t></hp:run></hp:p>"
            "<hp:p><hp:run><hp:t>비눗물은 염기성이다.</hp:t></hp:run></hp:p>"
            "</hp:subList></hp:endNote></hp:ctrl></hp:run></hp:p>"
            "<hp:p><hp:run><hp:t>① 비눗물 ② 식초</hp:t></hp:run></hp:p>"
            "</hs:sec>"
        ),
    )

    extracted = extract_hwpx_text(data)
    items = structure_text(source_name="science.hwpx", text=extracted)

    assert extracted.startswith("1. 산성 물질을 고르시오.")
    assert "\n\n2. 염기성 물질을 고르시오." in extracted
    assert len(items) == 2
    assert items[0].choices == ["① 비눗물", "② 식초"]
    assert items[0].answer == "②"
    assert items[0].explanation == "식초는 산성이다."
    assert "식초는 산성이다." not in items[0].prompt


def test_hwpx_extracts_native_picture_for_its_numbered_question():
    # Valid 1x1 PNG; display size comes from the HWPX picture frame.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f"
        b"\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    data = _hwpx_with_text(
        preview="",
        section=(
            '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
            '<hp:p><hp:run><hp:t>그림을 보고 고르시오.</hp:t>'
            '<hp:ctrl><hp:endNote number="3"><hp:subList>'
            '<hp:p><hp:run><hp:t>정답 ①</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run><hp:t>그림의 반응은 산화이다.</hp:t></hp:run></hp:p>'
            "</hp:subList></hp:endNote></hp:ctrl></hp:run></hp:p>"
            '<hp:p><hp:run><hp:pic><hp:img binaryItemIDRef="image2"/>'
            '<hp:sz width="9000" height="5000"/></hp:pic></hp:run></hp:p>'
            "</hs:sec>"
        ),
        extra_members={"BinData/image2.png": png},
    )

    visuals = extract_hwpx_question_images(data)

    assert len(visuals) == 1
    assert visuals[0]["question_number"] == 3
    assert visuals[0]["source_member"] == "BinData/image2.png"
    assert visuals[0]["mime"] == "image/png"
    assert visuals[0]["data"] == png


def test_hwpx_profile_detects_dominant_teacher_typography_and_two_column_layout():
    data = _hwpx_with_text(
        preview="",
        header=(
            '<hh:head xmlns:hh="urn:head">'
            '<hh:fontfaces><hh:fontface lang="HANGUL">'
            '<hh:font id="3" face="한겨레결체"/>'
            "</hh:fontface></hh:fontfaces>"
            '<hh:charProperties><hh:charPr id="8" height="900">'
            '<hh:fontRef hangul="3"/><hh:ratio hangul="96"/>'
            '<hh:spacing hangul="-4"/>'
            "</hh:charPr></hh:charProperties>"
            '<hh:paraProperties><hh:paraPr id="7">'
            '<hh:lineSpacing type="PERCENT" value="150"/>'
            "</hh:paraPr></hh:paraProperties>"
            "</hh:head>"
        ),
        section=(
            '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
            '<hp:p paraPrIDRef="7"><hp:run charPrIDRef="8">'
            '<hp:secPr><hp:pagePr width="59528" height="84186">'
            '<hp:margin top="5668" bottom="4252" left="2834" right="2834"/>'
            "</hp:pagePr><hp:colPr colCount=\"2\" sameGap=\"2268\">"
            '<hp:colLine type="DASH"/></hp:colPr></hp:secPr>'
            "<hp:t>산화 환원 반응 문제 본문</hp:t>"
            "</hp:run></hp:p></hs:sec>"
        ),
    )

    profile = extract_hwpx_document_profile(data)

    assert profile["body_font_family"] == "한겨레결체"
    assert profile["body_size_pt"] == 9
    assert profile["body_width_ratio_percent"] == 96
    assert profile["body_letter_spacing_percent"] == -4
    assert profile["line_spacing_percent"] == 150
    assert profile["column_count"] == 2
    assert profile["column_gap_mm"] == 8
    assert profile["center_line_style"] == "DASH"
    assert profile["margins_mm"] == {
        "top": 20,
        "bottom": 15,
        "left": 10,
        "right": 10,
    }


def test_generated_hwpx_persists_hancom_width_spacing_and_dashed_center_line():
    generated = build_hwpx_exam_document(
        title="산화와 환원",
        meta_lines=[],
        items=[
            {
                "number": 1,
                "prompt": "산소를 얻는 반응을 고르시오.",
                "choices": ["① 반응 A", "② 반응 B"],
                "answer": "①",
                "explanation": "반응 A는 산화 반응이다.",
            },
        ],
        document_style={
            "title_font": {"family_name": "한겨레결체"},
            "body_font": {"family_name": "한겨레결체"},
            "title_size_pt": 14,
            "body_size_pt": 9,
            "body_width_ratio_percent": 96,
            "body_letter_spacing_percent": -4,
            "line_spacing_percent": 150,
            "page_layout": {
                "page_width_mm": 210,
                "page_height_mm": 297,
                "margin_top_mm": 20,
                "margin_bottom_mm": 15,
                "margin_left_mm": 10,
                "margin_right_mm": 10,
                "column_count": 2,
                "column_gap_mm": 8,
                "center_line": True,
                "center_line_style": "DASH",
            },
        },
    )

    profile = extract_hwpx_document_profile(generated)

    assert profile["body_font_family"] == "한겨레결체"
    assert profile["body_size_pt"] == 9
    assert profile["body_width_ratio_percent"] == 96
    assert profile["body_letter_spacing_percent"] == -4
    assert profile["line_spacing_percent"] == 150
    assert profile["column_count"] == 2
    assert profile["center_line_style"] == "DASH"


def test_legacy_hwp_endnote_anchor_reorders_teacher_solution_after_choices():
    extracted = _rebuild_hwp_question_blocks([
        f"산성 물질에 대한 자료이다.{_HWP_ENDNOTE_ANCHOR}",
        "유형출제",
        "정답 ②",
        "식초는 산성이다.",
        "[오답]",
        "비눗물은 염기성이다.",
        "이에 대한 설명으로 옳은 것은?",
        "① 비눗물",
        "② 식초",
        f"염기성 물질에 대한 자료이다.{_HWP_ENDNOTE_ANCHOR}",
        "정답 ①",
        "비눗물은 염기성이다.",
        "옳은 것을 고르시오.",
        "① 비눗물",
        "② 식초",
    ])
    items = structure_text(source_name="legacy.hwp", text=extracted)

    assert len(items) == 2
    assert items[0].prompt.startswith("산성 물질에 대한 자료이다.")
    assert items[0].choices == ["① 비눗물", "② 식초"]
    assert items[0].answer == "②"
    assert items[0].explanation.endswith("비눗물은 염기성이다.")
    assert items[1].answer == "①"


def test_source_explanations_are_job_scoped_only_after_rights_confirmation():
    base_profile = {
        "name": "내 문체",
        "style_examples": [{
            "problem": "기존 문제",
            "answer": "①",
            "explanation": "기존에 직접 작성하고 승인한 해설 문장입니다.",
        }],
    }
    items = [{
        "prompt": "산화 환원 문제",
        "answer": "②",
        "explanation": "산소를 얻는 물질은 산화되고 산소를 잃는 물질은 환원됩니다.",
    }]

    denied = augment_voice_profile_with_source_items(
        base_profile,
        items=items,
        enabled=True,
        rights_confirmed=False,
    )
    augmented = augment_voice_profile_with_source_items(
        base_profile,
        items=items,
        enabled=True,
        rights_confirmed=True,
    )

    assert denied is base_profile
    assert "ephemeral_source_style_sample_count" not in base_profile
    assert augmented is not base_profile
    assert augmented["ephemeral_source_style_sample_count"] == 1
    assert augmented["style_examples"][0]["problem"] == "산화 환원 문제"


def test_transfer_package_preserves_uploaded_original_bytes():
    source = _hwpx_with_text(
        preview="",
        section=(
            '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
            "<hp:p><hp:run><hp:t>원본 보존 확인</hp:t></hp:run></hp:p>"
            "</hs:sec>"
        ),
    )
    upload = BytesIO(source)
    upload.name = "교사 원본.hwpx"

    package = build_transfer_package(
        payload={
            "title": "원본 보존 테스트",
            "class_name": "",
            "subject": "과학",
            "auto_explanations": False,
        },
        source_files=[upload],
    )

    with ZipFile(BytesIO(package.data)) as archive:
        assert (
            archive.read("06_업로드원본_그대로/교사 원본.hwpx")
            == source
        )


def test_transfer_uses_uploaded_teacher_explanations_as_ephemeral_voice_only():
    source = _hwpx_with_text(
        preview="",
        section=(
            '<hs:sec xmlns:hs="urn:section" xmlns:hp="urn:paragraph">'
            "<hp:p><hp:run><hp:t>산성 물질을 고르시오.</hp:t>"
            '<hp:ctrl><hp:endNote number="1"><hp:subList>'
            "<hp:p><hp:run><hp:t>정답 ②</hp:t></hp:run></hp:p>"
            "<hp:p><hp:run><hp:t>식초는 수소 이온을 내놓으므로 산성 물질입니다.</hp:t></hp:run></hp:p>"
            "</hp:subList></hp:endNote></hp:ctrl></hp:run></hp:p>"
            "<hp:p><hp:run><hp:t>① 비눗물 ② 식초</hp:t></hp:run></hp:p>"
            "</hs:sec>"
        ),
    )
    upload = BytesIO(source)
    upload.name = "내가 쓴 해설.hwpx"
    payload = {
        "title": "문체 적용 테스트",
        "subject": "과학",
        "auto_explanations": True,
        "learn_source_explanation_style": True,
        "source_style_rights_confirmed": True,
    }
    captured: dict[str, object] = {}

    def explanation_builder(_structure):
        captured["profile"] = payload.get("_resolved_voice_profile")
        return []

    build_transfer_package(
        payload=payload,
        source_files=[upload],
        explanation_builder=explanation_builder,
    )

    profile = captured["profile"]
    assert profile["ephemeral_source_style_sample_count"] == 1
    assert profile["style_examples"][0]["explanation"].startswith("식초는")
