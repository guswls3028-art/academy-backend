"""Generate or verify the committed OpenAPI schema deterministically."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
SCHEMA_DIR = BACKEND_DIR / "schema"
SCHEMA_PATH = SCHEMA_DIR / "openapi.json"
BASELINE_PATH = SCHEMA_DIR / "generation-baseline.json"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _generate() -> tuple[bytes, dict[str, int]]:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.schema")

    import django

    django.setup()

    from drf_spectacular.drainage import GENERATOR_STATS
    from drf_spectacular.generators import SchemaGenerator
    from drf_spectacular.renderers import OpenApiJsonRenderer
    from drf_spectacular.validation import validate_schema

    GENERATOR_STATS.reset()
    with GENERATOR_STATS.silence():
        schema = SchemaGenerator().get_schema(request=None, public=True)
    validate_schema(schema)
    rendered = OpenApiJsonRenderer().render(schema, renderer_context={})
    normalized = json.dumps(
        json.loads(rendered),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    counts = {
        "errors": sum(GENERATOR_STATS._error_cache.values()),
        "unique_errors": len(GENERATOR_STATS._error_cache),
        "warnings": sum(GENERATOR_STATS._warn_cache.values()),
        "unique_warnings": len(GENERATOR_STATS._warn_cache),
        "paths": len(schema.get("paths", {})),
        "components": len(schema.get("components", {}).get("schemas", {})),
    }
    return normalized, counts


def _verify_baseline(counts: dict[str, int]) -> list[str]:
    if not BASELINE_PATH.exists():
        return [f"missing generation baseline: {BASELINE_PATH.relative_to(BACKEND_DIR)}"]
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []
    for key in ("errors", "unique_errors", "warnings", "unique_warnings"):
        if counts[key] > int(baseline[key]):
            failures.append(f"{key} increased: current={counts[key]} baseline={baseline[key]}")
    for key in ("paths", "components"):
        if counts[key] < int(baseline[key]):
            failures.append(f"{key} decreased: current={counts[key]} baseline={baseline[key]}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if generated output or the quality baseline drifts.",
    )
    args = parser.parse_args()

    generated, counts = _generate()
    print(json.dumps(counts, sort_keys=True))

    if not args.check:
        SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_bytes(generated)
        print(f"wrote {SCHEMA_PATH.relative_to(BACKEND_DIR)}")
        return 0

    failures = _verify_baseline(counts)
    if not SCHEMA_PATH.exists():
        failures.append(f"missing committed schema: {SCHEMA_PATH.relative_to(BACKEND_DIR)}")
    elif SCHEMA_PATH.read_bytes() != generated:
        failures.append("schema/openapi.json is stale; run scripts/generate_openapi_schema.py")

    if failures:
        for failure in failures:
            print(f"OPENAPI_CONTRACT_FAIL: {failure}", file=sys.stderr)
        return 1
    print("OPENAPI_CONTRACT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
