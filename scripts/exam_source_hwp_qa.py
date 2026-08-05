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
    crop_problem_from_endnote,
    extract_document_endnotes,
)


def _preview_numbers(visuals) -> set[int]:
    if not visuals:
        return set()
    indexes = {0, len(visuals) // 2, len(visuals) - 1}
    return {int(visuals[index].number) for index in indexes}


def analyze_manifest(*, manifest_path: Path, output_dir: Path) -> dict:
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
            "missing_visual_numbers": [],
            "preview_numbers": [],
            "visual_dimensions": [],
        }
        try:
            extraction = extract_document_endnotes(
                str(source_path),
                str(document["display_name"]),
            )
            preview_numbers = _preview_numbers(extraction.visuals)
            report["control_count"] = len(extraction.control_numbers)
            report["visual_count"] = len(extraction.visuals)
            report["missing_visual_numbers"] = list(extraction.missing_visual_numbers)
            report["preview_numbers"] = sorted(preview_numbers)
            report["visual_dimensions"] = [
                {
                    "number": int(visual.number),
                    "width": int(visual.width),
                    "height": int(visual.height),
                    "pictures": int(visual.picture_count),
                }
                for visual in extraction.visuals
            ]
            if not extraction.visuals:
                report["status"] = "no_numbered_visuals"
            elif extraction.missing_visual_numbers:
                report["status"] = "paired_problem_file_required"
            else:
                report["status"] = "combined_document_ready"

            for visual in extraction.visuals:
                if int(visual.number) not in preview_numbers:
                    continue
                stem = f"{source_id}-q{int(visual.number):03d}"
                (preview_dir / f"{stem}-full.png").write_bytes(visual.png_bytes)
                cropped = crop_problem_from_endnote(visual.png_bytes)
                (preview_dir / f"{stem}-problem.png").write_bytes(cropped)
                with Image.open(BytesIO(cropped)) as image:
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
    args = parser.parse_args()
    payload = analyze_manifest(
        manifest_path=args.manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if "error" not in payload["summary"]["status_counts"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
