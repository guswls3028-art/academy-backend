from __future__ import annotations

from unittest.mock import patch


def test_ai_contract_delegates_lazily():
    from apps.domains.ai.contracts import dispatch_job

    with patch("apps.domains.ai.gateway.dispatch_job", return_value="job") as delegate:
        assert dispatch_job(job_type="test", payload={}) == "job"
    delegate.assert_called_once_with(job_type="test", payload={})


def test_results_contract_preserves_score_lease_arguments():
    from apps.domains.results.contracts import require_score_edit_lease

    request = object()
    with patch(
        "apps.domains.results.guards.score_edit_lease_guard.require_score_edit_lease",
        return_value="lease",
    ) as delegate:
        assert require_score_edit_lease(request, session_id=7, exam_id=9) == "lease"
    delegate.assert_called_once_with(request, session_id=7, exam_id=9)


def test_tools_contracts_delegate_without_eager_document_imports():
    from apps.domains.tools.contracts import (
        beta_run_id_from_job_payload,
        build_hwpx_editable_wrong_note_document,
        settle_beta_run,
        settle_explanation_step_failure,
    )

    with (
        patch(
            "apps.domains.tools.problem_studio.beta_access.beta_run_id_from_job_payload",
            return_value="run-id",
        ),
        patch(
            "apps.domains.tools.problem_studio.beta_access.settle_beta_run",
            return_value="settled",
        ),
        patch(
            "apps.domains.tools.problem_studio.explanation_workflow.settle_explanation_step_failure",
            return_value="failed",
        ),
        patch(
            "apps.domains.tools.problem_studio.hwpx_writer.build_hwpx_editable_wrong_note_document",
            return_value=b"hwpx",
        ),
    ):
        assert beta_run_id_from_job_payload({"beta_run_id": "run-id"}) == "run-id"
        assert settle_beta_run(run_id="run-id") == "settled"
        assert settle_explanation_step_failure(job_id="job-id") == "failed"
        assert build_hwpx_editable_wrong_note_document(items=[]) == b"hwpx"


def test_video_contracts_preserve_sorting_and_embed_results():
    from apps.domains.video.contracts import sort_videos_for_playlist, youtube_embed_url

    rows = [object()]
    with (
        patch("apps.domains.video.sorting.sort_videos_for_playlist", return_value=rows),
        patch("apps.domains.video.youtube.youtube_embed_url", return_value="embed"),
    ):
        assert sort_videos_for_playlist(rows) is rows
        assert youtube_embed_url("video-id") == "embed"


def test_matchup_contracts_preserve_tenant_scope():
    from apps.domains.matchup.contracts import (
        iter_problem_studio_reference_texts,
        iter_problem_studio_teacher_comments,
    )

    with (
        patch(
            "apps.domains.matchup.selectors.iter_problem_studio_reference_texts",
            return_value=iter(["problem"]),
        ) as references,
        patch(
            "apps.domains.matchup.selectors.iter_problem_studio_teacher_comments",
            return_value=iter(["comment"]),
        ) as comments,
    ):
        assert list(iter_problem_studio_reference_texts(tenant_id=3)) == ["problem"]
        assert list(iter_problem_studio_teacher_comments(tenant_id=3)) == ["comment"]
    references.assert_called_once_with(tenant_id=3)
    comments.assert_called_once_with(tenant_id=3)
