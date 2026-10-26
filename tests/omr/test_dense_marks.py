"""Dense marks must not become blank when no unmarked noise pool remains."""
import numpy as np
import pytest

from academy.adapters.ai.omr.engine import detect_omr_answers_v7
from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from tests.omr.test_omr_pipeline import create_synthetic_multi_mark_omr


@pytest.mark.parametrize("question_count", [20, 60])
@pytest.mark.parametrize("intensity", [0, 95, 160])
def test_four_and_five_marks_preserve_every_selected_choice(question_count, intensity):
    meta = build_omr_meta(question_count=question_count, n_choices=5)
    marks = {
        str(count): {str(choice): intensity for choice in range(1, count + 1)}
        for count in range(1, 6)
    }
    image = create_synthetic_multi_mark_omr(meta, marks)
    results = detect_omr_answers_v7(image_bgr=image, meta=meta)
    for count, result in enumerate(results[:5], 1):
        assert result.status == "ok"
        assert result.marking == ("single" if count == 1 else "multi")
        assert result.detected == [str(choice) for choice in range(1, count + 1)]
    assert all(result.status == "blank" for result in results[5:])


@pytest.mark.parametrize("background", [0, 95, 160, 255])
def test_uniform_darkness_is_not_evidence_of_dense_marks(background):
    meta = build_omr_meta(question_count=20, n_choices=5)
    image = np.full((1240, 1754, 3), background, dtype=np.uint8)
    results = detect_omr_answers_v7(image_bgr=image, meta=meta)
    assert all(result.status == "blank" and result.detected == [] for result in results)
