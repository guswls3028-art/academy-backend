"""Run the real Ymath source corpus through an isolated Academy API tenant.

The runner intentionally stops at segmentation review.  Teacher-authored
problem crops and explanations must be inspected before the separate approval
endpoint is called; a queue completion alone is not a quality approval.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests


SCENARIO_PREFIX = "qa-ymath-realuse-"
TERMINAL_JOB_STATUSES = {
    "DONE",
    "FAILED",
    "REJECTED_BAD_INPUT",
    "FALLBACK_TO_GPU",
    "REVIEW_REQUIRED",
}
SUPPORTED_COMBINED_STATUSES = {"combined_document_ready"}
SOURCE_REANALYSIS_STATUSES = {
    "question_count_mismatch",
    "answer_coverage_incomplete",
    "teacher_explanation_coverage_incomplete",
    "job_failed",
    "conversion_required",
    "unsafe_partial_acceptance",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def assert_safe_target(api_base_url: str, tenant_code: str) -> None:
    if not tenant_code.startswith(SCENARIO_PREFIX):
        raise ValueError(f"tenant code must start with {SCENARIO_PREFIX!r}")
    parsed = urlparse(api_base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("real-use execution requires a loopback HTTP URL connected to the SSM-only development API")


def build_source_plan(
    manifest: dict[str, Any],
    hwp_qa: dict[str, Any],
    pairings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    qa_by_source = {
        str(item["source_id"]): item
        for item in hwp_qa.get("items", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    pairings = pairings or {}
    documents_by_source = {
        str(item["source_id"]): item
        for item in manifest.get("documents", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    consumed_by: dict[str, str] = {}
    for primary_id, pairing in pairings.items():
        if not isinstance(pairing, dict):
            continue
        consumed_ids = list(pairing.get("consumed_source_ids") or [])
        explanation_id = pairing.get("explanation_source_id")
        if explanation_id:
            consumed_ids.append(explanation_id)
        for consumed_id in consumed_ids:
            consumed_id = str(consumed_id)
            if consumed_id != str(primary_id):
                consumed_by[consumed_id] = str(primary_id)
    plan = []
    for document in manifest.get("documents", []):
        if not isinstance(document, dict):
            continue
        source_id = str(document.get("source_id") or "")
        extension = str(document.get("extension") or "").lower()
        source_path = Path(str(document.get("extracted_path") or ""))
        item = {
            "source_id": source_id,
            "display_name": str(document.get("display_name") or source_path.name),
            "category": str(document.get("category") or "exam"),
            "source_path": str(source_path),
            "sha256": str(document.get("sha256") or ""),
            "size": int(document.get("size") or 0),
        }
        if source_id in consumed_by and source_id not in pairings:
            item.update(route="consumed_by_pair", consumed_by=consumed_by[source_id])
            plan.append(item)
            continue
        if extension == ".pdf":
            item.update(route="problem_only", upload_path=str(source_path))
        elif extension in {".hwp", ".hwpx"}:
            qa = qa_by_source.get(source_id)
            if not qa:
                item.update(route="blocked", reason="missing_hwp_structure_qa")
            elif qa.get("status") in SUPPORTED_COMBINED_STATUSES:
                item.update(
                    route="combined_document",
                    upload_path=str(source_path),
                    detected_question_count=int(qa.get("control_count") or 0),
                )
            else:
                raw_pairing = pairings.get(source_id)
                paired_path: Path | None = None
                explanation_path = source_path
                explanation_qa = qa
                consumed_source_ids = [source_id]
                if isinstance(raw_pairing, str):
                    paired_path = Path(raw_pairing)
                elif isinstance(raw_pairing, dict):
                    raw_problem_path = raw_pairing.get("problem_path")
                    paired_path = Path(raw_problem_path) if raw_problem_path else None
                    explanation_source_id = str(raw_pairing.get("explanation_source_id") or source_id)
                    explanation_document = documents_by_source.get(explanation_source_id)
                    explanation_qa = qa_by_source.get(explanation_source_id)
                    if explanation_document:
                        explanation_path = Path(str(explanation_document.get("extracted_path") or ""))
                    consumed_source_ids = [
                        str(value)
                        for value in raw_pairing.get(
                            "consumed_source_ids",
                            [source_id, explanation_source_id],
                        )
                    ]
                if paired_path:
                    item.update(
                        route="paired_problem_and_explanation",
                        upload_path=str(paired_path),
                        explanation_path=str(explanation_path),
                        detected_question_count=int(
                            (raw_pairing.get("expected_question_count") if isinstance(raw_pairing, dict) else None)
                            or qa.get("control_count")
                            or 0
                        ),
                        extracted_explanation_count=int((explanation_qa or {}).get("visual_count") or 0),
                        consumed_source_ids=consumed_source_ids,
                    )
                else:
                    item.update(
                        route="blocked",
                        reason="clean_problem_pdf_required",
                        upload_path=str(source_path),
                        expected_execution_status="conversion_required",
                        missing_visual_numbers=list(qa.get("missing_visual_numbers") or []),
                    )
        else:
            item.update(route="blocked", reason="unsupported_extension")
        raw_support = pairings.get(source_id)
        if isinstance(raw_support, dict) and item.get("route") not in {
            "blocked",
            "consumed_by_pair",
        }:
            if raw_support.get("answer_path"):
                item["answer_path"] = str(Path(str(raw_support["answer_path"])))
            if raw_support.get("explanation_path") and not item.get(
                "explanation_path"
            ):
                item["explanation_path"] = str(
                    Path(str(raw_support["explanation_path"]))
                )
        plan.append(item)
    return plan


class AcademyClient:
    def __init__(
        self,
        *,
        base_url: str,
        tenant_code: str,
        username: str,
        password: str,
        timeout: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.tenant_code = tenant_code
        self.timeout = timeout
        self.session = requests.Session()
        response = self.session.post(
            f"{self.base_url}/api/v1/token/",
            json={
                "username": username,
                "password": password,
                "tenant_code": tenant_code,
            },
            headers={"X-Tenant-Code": tenant_code},
            timeout=timeout,
        )
        self._raise(response)
        token = response.json().get("access")
        if not token:
            raise RuntimeError("login response did not include an access token")
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "X-Tenant-Code": tenant_code,
            }
        )

    @staticmethod
    def _raise(response: requests.Response) -> None:
        if response.ok:
            return
        body = response.text[:2_000]
        raise RuntimeError(f"HTTP {response.status_code} {response.url}: {body}")

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
        self._raise(response)
        return response.json()

    def get_json(self, path: str) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}{path}", timeout=self.timeout)
        self._raise(response)
        return response.json()

    def upload_source(
        self,
        *,
        exam_id: int,
        upload_path: Path,
        answer_path: Path | None,
        explanation_path: Path | None,
    ) -> dict[str, Any]:
        handles = []
        try:
            primary = upload_path.open("rb")
            handles.append(primary)
            files: dict[str, Any] = {
                "file": (upload_path.name, primary, _content_type(upload_path)),
            }
            if answer_path is not None:
                answer = answer_path.open("rb")
                handles.append(answer)
                files["answer_file"] = (
                    answer_path.name,
                    answer,
                    _content_type(answer_path),
                )
            if explanation_path is not None:
                explanation = explanation_path.open("rb")
                handles.append(explanation)
                files["explanation_file"] = (
                    explanation_path.name,
                    explanation,
                    _content_type(explanation_path),
                )
            response = self.session.post(
                f"{self.base_url}/api/v1/exams/pdf-extract/",
                data={"exam_id": str(exam_id)},
                files=files,
                timeout=max(self.timeout, 300),
            )
            self._raise(response)
            return response.json()
        finally:
            for handle in handles:
                handle.close()


def _content_type(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".hwp": "application/x-hwp",
        ".hwpx": "application/vnd.hancom.hwpx",
    }.get(path.suffix.lower(), "application/octet-stream")


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _product_title(item: dict[str, Any]) -> str:
    return f"[Ymath 실자료 QA:{item['source_id']}] {item['display_name']}"[:200]


def _page_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return []


def _find_existing_product(
    client: AcademyClient,
    item: dict[str, Any],
    session_id: int,
    title: str,
) -> tuple[str, int] | None:
    if item["category"] == "workbook":
        payload = client.get_json(f"/api/v1/homeworks/?session_id={session_id}&page_size=100")
        matches = [row for row in _page_results(payload) if str(row.get("title") or "") == title]
        if len(matches) > 1:
            raise RuntimeError(f"multiple workbook products match deterministic title: {title}")
        if not matches:
            return None
        ensured = client.post_json(f"/api/v1/homeworks/{int(matches[0]['id'])}/source-exam/", {})
        return "homework", int(ensured["source_exam_id"])
    payload = client.get_json(
        f"/api/v1/exams/?exam_type=regular&session_id={session_id}&page_size=100"
    )
    matches = [row for row in _page_results(payload) if str(row.get("title") or "") == title]
    if len(matches) > 1:
        raise RuntimeError(f"multiple exam products match deterministic title: {title}")
    if not matches:
        return None
    return "exam", int(matches[0]["id"])


def _recover_product_after_disconnect(
    client: AcademyClient,
    item: dict[str, Any],
    session_id: int,
    title: str,
) -> tuple[str, int] | None:
    for attempt in range(30):
        try:
            recovered = _find_existing_product(client, item, session_id, title)
        except requests.ConnectionError:
            recovered = None
        if recovered is not None:
            return recovered
        if attempt < 29:
            time.sleep(1)
    return None


def _create_product(client: AcademyClient, item: dict[str, Any], session_id: int) -> tuple[str, int]:
    title = _product_title(item)
    existing = _find_existing_product(client, item, session_id, title)
    if existing is not None:
        return existing
    if item["category"] == "workbook":
        try:
            homework = client.post_json(
                "/api/v1/homeworks/",
                {"session": session_id, "title": title, "homework_type": "regular"},
            )
        except requests.ConnectionError:
            recovered = _recover_product_after_disconnect(client, item, session_id, title)
            if recovered is not None:
                return recovered
            raise
        ensured = client.post_json(f"/api/v1/homeworks/{int(homework['id'])}/source-exam/", {})
        return "homework", int(ensured["source_exam_id"])
    try:
        exam = client.post_json(
            "/api/v1/exams/",
            {
                "title": title,
                "exam_type": "regular",
                "session_id": session_id,
                "grading_mode": "written",
                "manual_grading_method": "correctness",
                "max_score": 100,
                "pass_score": 0,
            },
        )
    except requests.ConnectionError:
        recovered = _recover_product_after_disconnect(client, item, session_id, title)
        if recovered is not None:
            return recovered
        raise
    return "exam", int(exam["id"])


def _wait_for_job(client: AcademyClient, job_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    delay = 2.0
    while time.monotonic() < deadline:
        payload = client.get_json(f"/api/v1/jobs/{job_id}/")
        if str(payload.get("status") or "").upper() in TERMINAL_JOB_STATUSES:
            return payload
        time.sleep(delay)
        delay = min(delay * 1.3, 10.0)
    raise TimeoutError(f"job did not finish within {timeout_seconds}s: {job_id}")


def _wait_for_review(client: AcademyClient, exam_id: int, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    delay = 2.0
    while time.monotonic() < deadline:
        review = client.get_json(f"/api/v1/exams/{exam_id}/segmentation-review/")
        if str(review.get("status") or "") in {
            "review_required",
            "conversion_required",
            "failed",
            "ready",
        }:
            return review
        time.sleep(delay)
        delay = min(delay * 1.3, 10.0)
    raise TimeoutError(f"segmentation review did not finish within {timeout_seconds}s: {exam_id}")


def _recovered_job_from_review(review: dict[str, Any]) -> dict[str, Any]:
    status = str(review.get("status") or "")
    return {
        "status": "FAILED" if status == "failed" else "DONE",
        "result": {"conversion_required": True} if status == "conversion_required" else {},
    }


def execute_item(
    *,
    client: AcademyClient,
    item: dict[str, Any],
    session_id: int,
    job_timeout: int,
    prior: dict[str, Any],
    checkpoint,
) -> dict[str, Any]:
    started = time.time()
    state = {**prior, **item}
    state.pop("error", None)
    product_type = str(state.get("product_type") or "")
    exam_id = int(state.get("exam_id") or 0)
    if not exam_id:
        product_type, exam_id = _create_product(client, item, session_id)
        state.update(
            execution_status="product_created",
            product_type=product_type,
            exam_id=exam_id,
        )
        checkpoint(state)
    previous_execution_status = str(prior.get("execution_status") or "")
    reanalyze_source = (
        previous_execution_status in SOURCE_REANALYSIS_STATUSES
        or previous_execution_status.startswith("unexpected_review_status:")
    )
    job_id = "" if reanalyze_source else str(state.get("job_id") or "")
    job: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    if not job_id:
        existing_review = client.get_json(f"/api/v1/exams/{exam_id}/segmentation-review/")
        if not reanalyze_source and str(existing_review.get("status") or "") != "none":
            review = _wait_for_review(client, exam_id, job_timeout)
            job = _recovered_job_from_review(review)
            state.update(execution_status="job_recovered_from_review", upload_recovered=True)
            checkpoint(state)
        else:
            upload = client.upload_source(
                exam_id=exam_id,
                upload_path=Path(item["upload_path"]),
                answer_path=(
                    Path(item["answer_path"]) if item.get("answer_path") else None
                ),
                explanation_path=(
                    Path(item["explanation_path"]) if item.get("explanation_path") else None
                ),
            )
            job_id = str(upload.get("job_id") or "")
            if not job_id:
                raise RuntimeError("upload response did not include a job id")
            state.update(
                execution_status="job_submitted",
                job_id=job_id,
                source_reanalysis=reanalyze_source,
            )
            checkpoint(state)
    if job is None:
        job = _wait_for_job(client, job_id, job_timeout)
    if review is None:
        review = client.get_json(f"/api/v1/exams/{exam_id}/segmentation-review/")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    proposal_count = len(review.get("items") or [])
    explanation_count = sum(
        1 for review_item in review.get("items") or [] if review_item.get("has_teacher_explanation")
    )
    answer_count = sum(
        1
        for review_item in review.get("items") or []
        if str(review_item.get("answer") or "").strip()
    )
    expected = int(item.get("detected_question_count") or 0)
    quality_status = "review_required"
    expects_conversion = item.get("expected_execution_status") == "conversion_required"
    if str(job.get("status") or "").upper() not in {"DONE", "REVIEW_REQUIRED"}:
        quality_status = "job_failed"
    elif result.get("conversion_required"):
        quality_status = "source_remediation_required" if expects_conversion else "conversion_required"
    elif expects_conversion:
        quality_status = "unsafe_partial_acceptance"
    elif str(review.get("status") or "") != "review_required":
        quality_status = f"unexpected_review_status:{review.get('status')}"
    elif expected and proposal_count != expected:
        quality_status = "question_count_mismatch"
    elif item.get("answer_path") and answer_count != proposal_count:
        quality_status = "answer_coverage_incomplete"
    elif item.get("route") == "paired_problem_and_explanation" and explanation_count != proposal_count:
        quality_status = "teacher_explanation_coverage_incomplete"
    return {
        **state,
        "execution_status": quality_status,
        "product_type": product_type,
        "exam_id": exam_id,
        "job_id": job_id,
        "job_status": job.get("status"),
        "job_result": result,
        "review_status": review.get("status"),
        "proposal_count": proposal_count,
        "answer_count": answer_count,
        "teacher_explanation_count": explanation_count,
        "review_items": review.get("items") or [],
        "elapsed_seconds": round(time.time() - started, 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--tenant-code", default="qa-ymath-realuse-20260805")
    parser.add_argument("--teacher-username", default="ymath-qa-teacher")
    parser.add_argument("--password-env", default="YMATH_REALUSE_SCENARIO_PASSWORD")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--hwp-qa", type=Path, required=True)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--pairings", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--job-timeout", type=int, default=1_800)
    parser.add_argument("--http-timeout", type=int, default=60)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def _executable_plan_items(
    plan: list[dict[str, Any]],
    output_items: dict[str, dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, item)
        for index, item in enumerate(plan)
        if item["route"] != "consumed_by_pair"
        and item.get("upload_path")
        and output_items[item["source_id"]].get("execution_status")
        not in {"review_required", "source_remediation_required"}
    ]


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest)
    hwp_qa = load_json(args.hwp_qa)
    pairings = load_json(args.pairings) if args.pairings else {}
    plan = build_source_plan(manifest, hwp_qa, pairings)
    if args.max_items > 0:
        plan = plan[: args.max_items]
    blocked = [item for item in plan if item["route"] == "blocked"]
    previous: dict[str, Any] = {}
    if args.output.exists():
        loaded = load_json(args.output)
        if loaded.get("tenant_code") == args.tenant_code:
            previous = loaded
    previous_items = previous.get("items") if isinstance(previous.get("items"), dict) else {}
    output: dict[str, Any] = {
        "tenant_code": args.tenant_code,
        "manifest_summary": manifest.get("summary", {}),
        "plan_count": len(plan),
        "blocked_count": len(blocked),
        "items": {
            item["source_id"]: {
                **(
                    previous_items.get(item["source_id"], {})
                    if isinstance(previous_items.get(item["source_id"]), dict)
                    else {}
                ),
                **item,
            }
            for item in plan
        },
    }
    _atomic_write(args.output, output)
    if args.plan_only:
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0

    assert_safe_target(args.api_base_url, args.tenant_code)
    if args.scenario is None:
        raise ValueError("--scenario is required for execution")
    scenario = load_json(args.scenario)
    password = str(os.environ.get(args.password_env) or "")
    if not password:
        raise RuntimeError(f"{args.password_env} must be set")
    session_ids = [int(value) for value in scenario.get("session_ids", [])]
    if not session_ids:
        raise ValueError("scenario JSON has no session_ids")
    executable = _executable_plan_items(plan, output["items"])
    lock = threading.Lock()
    local = threading.local()

    def client() -> AcademyClient:
        if not hasattr(local, "client"):
            local.client = AcademyClient(
                base_url=args.api_base_url,
                tenant_code=args.tenant_code,
                username=args.teacher_username,
                password=password,
                timeout=args.http_timeout,
            )
        return local.client

    def save_checkpoint(result: dict[str, Any]) -> None:
        with lock:
            output["items"][result["source_id"]] = result
            _atomic_write(args.output, output)

    def run(index: int, item: dict[str, Any]) -> dict[str, Any]:
        prior = output["items"].get(item["source_id"], {})
        try:
            return execute_item(
                client=client(),
                item=item,
                session_id=session_ids[index % len(session_ids)],
                job_timeout=args.job_timeout,
                prior=prior,
                checkpoint=save_checkpoint,
            )
        except Exception as exc:
            return {
                **prior,
                **item,
                "execution_status": "runner_error",
                "error": str(exc),
            }

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        futures = {
            pool.submit(run, plan_index, item): item
            for plan_index, item in executable
        }
        for future in as_completed(futures):
            result = future.result()
            with lock:
                output["items"][result["source_id"]] = result
                statuses: dict[str, int] = {}
                for current in output["items"].values():
                    status = str(current.get("execution_status") or current.get("reason") or "planned")
                    statuses[status] = statuses.get(status, 0) + 1
                output["status_counts"] = statuses
                _atomic_write(args.output, output)
            print(
                json.dumps(
                    {
                        "source_id": result["source_id"],
                        "status": result.get("execution_status"),
                        "proposal_count": result.get("proposal_count"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
    if blocked:
        return 2
    return (
        0
        if all(
            item.get("execution_status") == "review_required"
            for item in output["items"].values()
            if item.get("route") != "consumed_by_pair"
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
