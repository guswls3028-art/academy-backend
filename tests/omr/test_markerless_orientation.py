"""Scanner rotations must recover answers even when corner markers are lost."""
import cv2
import numpy as np
import pytest

from academy.adapters.ai.omr.engine import AnswerDetectConfig, detect_omr_answers_v7
from academy.adapters.ai.omr.warp import align_to_a4_landscape, rotate_cardinal_cw
from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from tests.omr.test_omr_full_pipeline import draw_omr_image


@pytest.mark.parametrize("question_count", [28, 34])
@pytest.mark.parametrize("rotation,correction", [(90, 270), (270, 90)])
@pytest.mark.parametrize("shift_x", [0, -95])
def test_portrait_without_corner_markers_recovers_answers(question_count, rotation, correction, shift_x):
    meta = build_omr_meta(question_count=question_count, n_choices=5)
    expected = {str(i): str(i % 5 + 1) for i in range(1, question_count + 1)}
    upright = draw_omr_image(meta, marks=expected)
    h, w = upright.shape[:2]
    # Scanner clipping removes the four orientation marks, but leaves the
    # printed internal anchors. No student identifier is needed for direction.
    for x in (0, w - 120):
        for y in (0, h - 120):
            upright[y:y + 120, x:x + 120] = 255
    upright = cv2.warpAffine(upright, np.float32([[1, 0, shift_x], [0, 1, 0]]), (w, h), borderValue=(255, 255, 255))
    result = align_to_a4_landscape(image_bgr=rotate_cardinal_cw(upright, rotation), meta=meta)
    assert result.method == "rotation_only"
    assert result.input_correction_rotation == correction
    answers = detect_omr_answers_v7(image_bgr=result.image, meta=meta, config=AnswerDetectConfig())
    assert {str(a.question_id): a.detected[0] for a in answers if a.status == "ok"} == expected


def test_blank_portrait_does_not_invent_orientation_evidence():
    meta = build_omr_meta(question_count=28, n_choices=5)
    result = align_to_a4_landscape(image_bgr=np.full((3508, 2480, 3), 255, np.uint8), meta=meta)
    assert result.method == "rotation_only"
    assert result.input_correction_rotation == 90


def test_equal_anchor_evidence_preserves_existing_direction():
    meta = build_omr_meta(question_count=28, n_choices=5)
    upright = draw_omr_image(meta)
    h, w = upright.shape[:2]
    for x in (0, w - 120):
        for y in (0, h - 120):
            upright[y:y + 120, x:x + 120] = 255
    ambiguous = np.minimum(upright, rotate_cardinal_cw(upright, 180))
    result = align_to_a4_landscape(image_bgr=rotate_cardinal_cw(ambiguous, 90), meta=meta)
    assert result.method == "rotation_only"
    assert result.input_correction_rotation == 90
