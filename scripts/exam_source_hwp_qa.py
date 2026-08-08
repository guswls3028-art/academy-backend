from __future__ import annotations

import argparse
import json
import sys
import time
from io import BytesIO
from pathlib import Path

from PIL import Image


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from academy.adapters.tools.hwp_endnote_images import (  # noqa: E402
    extract_document_endnotes,
)


def _preview_numbers(visuals, *, preview_all: bool = False) -> set[int]:
    if not visuals:
        return set()
    if preview_all:
        return {int(visual.number) for visual in visuals}
    indexes = {0, len(visuals) // 2, len(visuals) - 1}
    return {int(visuals[index].number) for index in indexes}


def analyze_manifest(
    *,
    manifest_path: Path,
    output_dir: Path,
    preview_all: bool = False,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_dir / "hwp-previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    items = []
    for document in manifest.get("documents") or []:
        extension = str(document.get("extension") or "").lower()
        if extension not in {".hwp", ".hwpx"}:
            continue
        item_started = time.perf_counter()
        source_id = str(document["source_id"])
        source_path = Path(document["extracted_path"])
        report = {
            "source_id": source_id,
            "display_name": document["display_name"],
            "category": document["category"],
            "extension": extension,
            "status": "error",
            "control_count": 0,
            "visual_count": 0,
            "safe_explanation_count": 0,
            "problem_visual_count": 0,
            "missing_visual_numbers": [],
            "missing_safe_explanation_numbers": [],
            "missing_problem_numbers": [],
            "preview_numbers": [],
            "visual_dimensions": [],
        }
        try:
            extraction = extract_document_endnotes(
                str(source_path),
                str(document["display_name"]),
                include_paired_reconstruction=True,
                include_problem_reconstruction=True,
            )
            safe_explanations = extraction.paired_visuals or extraction.visuals
            preview_numbers = _preview_numbers(
                safe_explanations,
                preview_all=preview_all,
            )
            report["control_count"] = len(extraction.control_numbers)
            report["visual_count"] = len(extraction.visuals)
            report["safe_explanation_count"] = len(safe_explanations)
            report["problem_visual_count"] = len(extraction.problem_visuals)
            report["missing_visual_numbers"] = list(extraction.missing_visual_numbers)
            report["missing_safe_explanation_numbers"] = list(
                extraction.missing_paired_visual_numbers
            )
            report["missing_problem_numbers"] = list(
                extraction.missing_problem_visual_numbers
            )
            report["preview_numbers"] = sorted(preview_numbers)
            report["visual_dimensions"] = [
                {
                    "number": int(visual.number),
                    "width": int(visual.width),
                    "height": int(visual.height),
                    "pictures": int(visual.picture_count),
                }
                for visual in safe_explanations
            ]
            if not safe_explanations:
                report["status"] = "no_numbered_visuals"
            elif (
                extraction.missing_paired_visual_numbers
                or extraction.missing_problem_visual_numbers
            ):
                report["status"] = "paired_problem_file_required"
            else:
                report["status"] = "combined_document_ready"

            problems_by_number = {
                int(visual.number): visual for visual in extraction.problem_visuals
            }
            for visual in safe_explanations:
                if int(visual.number) not in preview_numbers:
                    continue
                stem = f"{source_id}-q{int(visual.number):03d}"
                (preview_dir / f"{stem}-full.png").write_bytes(visual.png_bytes)
                problem = problems_by_number.get(int(visual.number))
                if problem is None:
                    continue
                (preview_dir / f"{stem}-problem.png").write_bytes(problem.png_bytes)
                with Image.open(BytesIO(problem.png_bytes)) as image:
                    report.setdefault("problem_preview_dimensions", []).append(
                        {
                            "number": int(visual.number),
                            "width": int(image.width),
                            "height": int(image.height),
                        }
                    )
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        report["elapsed_seconds"] = round(
            time.perf_counter() - item_started,
            3,
        )
        items.append(report)

    status_counts = {
        status: sum(1 for item in items if item["status"] == status)
        for status in sorted({str(item["status"]) for item in items})
    }
    summary = {
        "manifest": str(manifest_path.resolve()),
        "total": len(items),
        "status_counts": status_counts,
        "total_controls": sum(int(item["control_count"]) for item in items),
        "total_visuals": sum(int(item["visual_count"]) for item in items),
        "total_safe_explanations": sum(
            int(item["safe_explanation_count"]) for item in items
        ),
        "total_problem_visuals": sum(
            int(item["problem_visual_count"]) for item in items
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    payload = {"summary": summary, "items": items}
    (output_dir / "hwp-qa.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze HWP/HWPX endnote coverage from a source manifest.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--preview-all",
        action="store_true",
        help="Render every matched pair for a focused document audit.",
    )
    args = parser.parse_args()
    payload = analyze_manifest(
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        preview_all=args.preview_all,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if "error" not in payload["summary"]["status_counts"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
