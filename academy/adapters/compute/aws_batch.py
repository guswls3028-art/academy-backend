"""AWS Batch compute adapter."""

from __future__ import annotations

from typing import Any


class AwsBatchClientError(RuntimeError):
    """AWS Batch returned a client-side error response."""


def _batch_client(region: str):
    import boto3

    return boto3.client("batch", region_name=region)


def submit_video_batch_job(
    *,
    video_job_id: str,
    queue_name: str,
    job_definition: str,
    region: str,
) -> str | None:
    from botocore.exceptions import ClientError

    container_overrides = {
        "environment": [
            {"name": "VIDEO_JOB_ID", "value": str(video_job_id)},
        ],
    }

    try:
        resp = _batch_client(region).submit_job(
            jobName=f"video-{video_job_id[:8]}",
            jobQueue=queue_name,
            jobDefinition=job_definition,
            parameters={"job_id": str(video_job_id)},
            containerOverrides=container_overrides,
        )
    except ClientError as e:
        raise AwsBatchClientError(str(e)[:2000]) from e
    return resp.get("jobId")


def describe_batch_jobs(*, aws_batch_job_ids: list[str], region: str) -> list[dict[str, Any]]:
    resp = _batch_client(region).describe_jobs(jobs=aws_batch_job_ids)
    return list(resp.get("jobs") or [])


def get_batch_queue_primary_compute_environment_desired_vcpus(
    *,
    queue_name: str,
    region: str,
) -> int | None:
    client = _batch_client(region)
    resp = client.describe_job_queues(jobQueues=[queue_name])
    queues = resp.get("jobQueues") or []
    if not queues:
        return None

    ce_arn = None
    for order in queues[0].get("computeEnvironmentOrder") or []:
        if order.get("order") == 1:
            ce_arn = order.get("computeEnvironment")
            break
    if not ce_arn:
        return None

    ce_name = ce_arn.split("/")[-1] if "/" in ce_arn else ce_arn.split(":")[-1]
    ce_resp = client.describe_compute_environments(computeEnvironments=[ce_name])
    environments = ce_resp.get("computeEnvironments") or []
    if not environments:
        return None
    resources = environments[0].get("computeResources") or {}
    return int(resources.get("desiredvCpus") or 0)


def iter_batch_job_summaries(
    *,
    queue_name: str,
    job_status: str,
    region: str,
):
    paginator = _batch_client(region).get_paginator("list_jobs")
    for page in paginator.paginate(jobQueue=queue_name, jobStatus=job_status):
        yield from page.get("jobSummaryList") or []


def terminate_batch_job(*, aws_batch_job_id: str, reason: str, region: str) -> None:
    _batch_client(region).terminate_job(jobId=aws_batch_job_id, reason=reason[:256])
