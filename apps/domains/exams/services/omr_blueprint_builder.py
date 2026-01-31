# apps/domains/exams/services/omr_blueprint_builder.py
from __future__ import annotations

from typing import Any, Dict, Optional

from apps.domains.exams.dto.omr_blueprint import OMRBlueprint, QuestionBlueprint, ROI, Bubble, Point

# ✅ IMPORTANT
# OMR Objective v1의 "레이아웃/좌표"는 assets 도메인이 SSOT.
# exams는 "템플릿이 어떤 규격을 쓰는지"만 결정하고,
# 실제 meta는 assets의 /api/v1/assets/omr/objective/meta/ 에서 가져온다.
#
# 따라서 exams 쪽 Blueprint는 "assets meta를 proxy/캐싱해서 제공"하거나,
# 시험 템플릿에서 "question_count를 확정"하여 프론트/서브미션에서 참조하도록 한다.


class OMRBlueprintBuilder:
    """
    - exams 템플릿의 question_count 기준으로
      assets meta(JSON)를 그대로 반환하거나, 필요한 최소 필드만 normalize.
    """

    def __init__(self, fetch_assets_meta_fn):
        """
        fetch_assets_meta_fn(question_count:int) -> dict
        - 외부 의존을 주입해서 테스트/도메인 분리를 유지
        """
        self._fetch = fetch_assets_meta_fn

    def build_from_assets_meta(self, *, question_count: int) -> Dict[str, Any]:
        meta = self._fetch(int(question_count))
        if not isinstance(meta, dict) or meta.get("version") != "objective_v1":
            raise ValueError("Invalid OMR meta from assets")
        return meta

    def build_strict(self, *, question_count: int) -> OMRBlueprint:
        """
        (선택) meta를 DTO로 엄격 변환.
        지금은 assets meta 계약을 신뢰하는 구조라 strict는 보조용.
        """
        meta = self.build_from_assets_meta(question_count=question_count)
        qs = []
        for q in meta.get("questions", []) or []:
            choices = []
            for c in q.get("choices", []) or []:
                choices.append(
                    Bubble(
                        choice=c["choice"],
                        center=Point(x=float(c["center"]["x"]), y=float(c["center"]["y"])),
                        radius=float(c["radius"]),
                    )
                )
            qs.append(
                QuestionBlueprint(
                    question_number=int(q["question_number"]),
                    axis=q["axis"],
                    roi=ROI(
                        x=float(q["roi"]["x"]),
                        y=float(q["roi"]["y"]),
                        w=float(q["roi"]["w"]),
                        h=float(q["roi"]["h"]),
                    ),
                    choices=choices,
                )
            )

        return OMRBlueprint(
            version="objective_v1",
            units="mm",
            question_count=int(meta["question_count"]),
            page=meta.get("page") or {},
            identifier=meta.get("identifier"),
            questions=qs,
        )
