# PATH: apps/support/video/management/commands/validate_video_iam_expectations.py
"""
Print required AWS IAM actions per role for video Batch pipeline (static list from code usage).
No AWS calls; used to verify IAM policies match code requirements.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


# Derived from: batch_submit.py (submit_job, terminate_job), reconcile_batch_video_jobs.py (describe_jobs, list_jobs, terminate_job),
# ops_events.py (put_metric_data), batch_entrypoint.py / config (ssm:GetParameter), video_worker ECR/logs (execution role).
BATCH_SERVICE_ROLE_ACTIONS = [
    "batch:CreateComputeEnvironment",
    "batch:DeleteComputeEnvironment",
    "batch:DescribeComputeEnvironments",
    "batch:UpdateComputeEnvironment",
    "ec2:DescribeInstanceStatus",
    "ec2:DescribeInstances",
    "ec2:DescribeSubnets",
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeVpcs",
    "iam:PassRole",
]

ECS_TASK_EXECUTION_ROLE_ACTIONS = [
    "ecr:GetAuthorizationToken",
    "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
]

BATCH_JOB_ROLE_ACTIONS = [
    "ssm:GetParameter",
    "ecr:GetAuthorizationToken",
    "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
    "cloudwatch:PutMetricData",
]

# API server (submit_job, terminate_job) - used when running reconcile or retry from API.
API_OR_RECONCILE_BATCH_ACTIONS = [
    "batch:SubmitJob",
    "batch:TerminateJob",
    "batch:DescribeJobs",
    "batch:ListJobs",
]


class Command(BaseCommand):
    help = "Print required AWS IAM actions per role for video Batch (from code usage)"

    def add_arguments(self, parser):
        parser.add_argument("--role", type=str, choices=["batch-service", "ecs-execution", "batch-job", "api"], help="Print only this role")

    def handle(self, *args, **options):
        role = options.get("role")
        if not role or role == "batch-service":
            self.stdout.write("Role: academy-batch-service-role (Batch service role)")
            for a in sorted(BATCH_SERVICE_ROLE_ACTIONS):
                self.stdout.write(f"  {a}")
            self.stdout.write("")
        if not role or role == "ecs-execution":
            self.stdout.write("Role: academy-batch-ecs-task-execution-role (ECS task execution; ECR pull + logs)")
            for a in sorted(ECS_TASK_EXECUTION_ROLE_ACTIONS):
                self.stdout.write(f"  {a}")
            self.stdout.write("")
        if not role or role == "batch-job":
            self.stdout.write("Role: academy-video-batch-job-role (Batch job / container task role)")
            for a in sorted(BATCH_JOB_ROLE_ACTIONS):
                self.stdout.write(f"  {a}")
            self.stdout.write("")
        if not role or role == "api":
            self.stdout.write("Role: API server / reconcile runner (Batch submit, terminate, describe, list)")
            for a in sorted(API_OR_RECONCILE_BATCH_ACTIONS):
                self.stdout.write(f"  {a}")
