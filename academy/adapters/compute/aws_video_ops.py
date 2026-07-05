"""AWS read-only operations adapter for video validation commands."""

from __future__ import annotations

from typing import Any


def _client(service: str, *, region: str | None = None):
    import boto3

    if region:
        return boto3.client(service, region_name=region)
    return boto3.client(service)


def describe_batch_compute_environments(*, names: list[str], region: str) -> list[dict[str, Any]]:
    resp = _client("batch", region=region).describe_compute_environments(computeEnvironments=names)
    return list(resp.get("computeEnvironments") or [])


def describe_batch_job_queues(*, names: list[str], region: str) -> list[dict[str, Any]]:
    resp = _client("batch", region=region).describe_job_queues(jobQueues=names)
    return list(resp.get("jobQueues") or [])


def describe_batch_job_definitions(*, name: str, status: str, region: str) -> list[dict[str, Any]]:
    resp = _client("batch", region=region).describe_job_definitions(jobDefinitionName=name, status=status)
    return list(resp.get("jobDefinitions") or [])


def describe_event_rule(*, name: str, region: str) -> dict[str, Any]:
    return dict(_client("events", region=region).describe_rule(Name=name))


def list_event_rule_targets(*, rule_name: str, region: str) -> list[dict[str, Any]]:
    resp = _client("events", region=region).list_targets_by_rule(Rule=rule_name)
    return list(resp.get("Targets") or [])


def iam_role_exists(*, role_name: str) -> bool:
    _client("iam").get_role(RoleName=role_name)
    return True


def iam_instance_profile_exists(*, profile_name: str) -> bool:
    _client("iam").get_instance_profile(InstanceProfileName=profile_name)
    return True


def get_ssm_parameter_value(*, name: str, region: str, with_decryption: bool = False) -> str:
    resp = _client("ssm", region=region).get_parameter(Name=name, WithDecryption=with_decryption)
    return str((resp.get("Parameter") or {}).get("Value") or "")


def describe_cloudwatch_alarms(*, alarm_names: list[str], region: str) -> list[dict[str, Any]]:
    resp = _client("cloudwatch", region=region).describe_alarms(AlarmNames=alarm_names)
    return list(resp.get("MetricAlarms") or [])


def describe_ec2_route_tables_for_subnets(*, subnet_ids: list[str], region: str) -> list[dict[str, Any]]:
    resp = _client("ec2", region=region).describe_route_tables(
        Filters=[{"Name": "association.subnet-id", "Values": subnet_ids}]
    )
    return list(resp.get("RouteTables") or [])


def describe_ec2_subnets(*, subnet_ids: list[str], region: str) -> list[dict[str, Any]]:
    resp = _client("ec2", region=region).describe_subnets(SubnetIds=subnet_ids)
    return list(resp.get("Subnets") or [])


def describe_ec2_main_route_tables_for_vpcs(*, vpc_ids: list[str], region: str) -> list[dict[str, Any]]:
    resp = _client("ec2", region=region).describe_route_tables(
        Filters=[
            {"Name": "vpc-id", "Values": vpc_ids},
            {"Name": "association.main", "Values": ["true"]},
        ]
    )
    return list(resp.get("RouteTables") or [])
