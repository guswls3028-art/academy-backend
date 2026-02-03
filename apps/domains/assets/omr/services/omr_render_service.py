from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class OMRRenderService:
    """
    OMR PDF 렌더링 단일 진입점
    SSOT = renderer/v245_final.py
    """

    @staticmethod
    def render(
        *,
        question_count: int = 45,
        debug_grid: bool = False,
    ) -> Path:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.close()

        env = os.environ.copy()
        env["OMR_OUT"] = tmp.name
        env["OMR_QC"] = str(question_count)
        env["OMR_DEBUG_GRID"] = "1" if debug_grid else "0"

        subprocess.check_call(
            [
                "python",
                "-m",
                "apps.domains.assets.omr.renderer.v245_final",
            ],
            env=env,
        )

        return Path(tmp.name)
