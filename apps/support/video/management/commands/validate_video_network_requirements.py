"""
Validate Batch compute environment networking: subnets set; if private, NAT or VPC endpoints.
Uses AWS SDK where possible; otherwise prints DEPENDS ON MANUAL AWS CONSOLE CONFIG and required endpoints/routes.
"""

from __future__ import annotations

import logging
from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)

REGION = getattr(settings, "AWS_DEFAULT_REGION", None) or __import__("os").environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
COMPUTE_ENV_NAME = getattr(settings, "VIDEO_BATCH_COMPUTE_ENV_NAME", "academy-v1-video-batch-ce")


class Command(BaseCommand):
    help = "Validate video Batch compute environment network requirements"

    def handle(self, *args, **options):
        try:
            import boto3
        except ImportError:
            self.stdout.write(self.style.ERROR("boto3 required"))
            return

        client_batch = boto3.client("batch", region_name=REGION)
        client_ec2 = boto3.client("ec2", region_name=REGION)
        errors = []
        warnings = []

        # 1) Describe compute environment and get subnets
        try:
            resp = client_batch.describe_compute_environments(computeEnvironments=[COMPUTE_ENV_NAME])
            envs = resp.get("computeEnvironments") or []
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"describe_compute_environments failed: {e}"))
            return

        if not envs:
            errors.append(f"Compute environment '{COMPUTE_ENV_NAME}' not found")
            self._print_manual_network()
            self._output(errors, warnings)
            return

        ce = envs[0]
        cr = ce.get("computeResources") or {}
        subnets = cr.get("subnets") or []
        security_groups = cr.get("securityGroupIds") or []

        if not subnets:
            errors.append("Compute environment has no subnets set (subnets empty)")
        else:
            self.stdout.write(self.style.SUCCESS(f"Subnets: {', '.join(subnets)}"))
        if not security_groups:
            warnings.append("No security groups configured")

        # 2) Check if subnets are private (no IGW route to 0.0.0.0/0)
        if not subnets:
            self._print_manual_network()
            self._output(errors, warnings)
            return

        try:
            route_tables = client_ec2.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": subnets}]
            ).get("RouteTables", [])
            # Also get main route tables for VPCs of these subnets
            subnet_details = client_ec2.describe_subnets(SubnetIds=subnets).get("Subnets", [])
            vpc_ids = list({s["VpcId"] for s in subnet_details})
            main_tables = client_ec2.describe_route_tables(
                Filters=[
                    {"Name": "vpc-id", "Values": vpc_ids},
                    {"Name": "association.main", "Values": ["true"]},
                ]
            ).get("RouteTables", [])
            all_tables = route_tables + main_tables
            has_igw_route = False
            for rt in all_tables:
                for r in rt.get("Routes", []):
                    if r.get("GatewayId", "").startswith("igw-"):
                        has_igw_route = True
                        break
            if not has_igw_route:
                warnings.append("No IGW route found for Batch subnets (likely private subnets)")
                self.stdout.write(
                    "DEPENDS ON MANUAL AWS CONSOLE CONFIG (or verify via console):"
                )
                self.stdout.write(
                    "  - If subnets are private: ensure NAT Gateway (or NAT instance) route exists for 0.0.0.0/0 from Batch subnets, OR"
                )
                self.stdout.write(
                    "  - Use VPC endpoints so Batch nodes can reach: ECR (ecr.api, ecr.dkr), CloudWatch Logs (logs), S3 (s3) for ECR layers."
                )
                self.stdout.write(
                    "  Required VPC endpoints (interface): com.amazonaws.<region>.ecr.api, com.amazonaws.<region>.ecr.dkr, com.amazonaws.<region>.logs."
                )
                self.stdout.write(
                    "  Required VPC endpoint (gateway): com.amazonaws.<region>.s3 (for ECR image pull via S3)."
                )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Cannot determine route tables: {e}"))
            self.stdout.write("DEPENDS ON MANUAL AWS CONSOLE CONFIG:")
            self.stdout.write("  - Verify Batch compute environment subnets have outbound internet (NAT or IGW).")
            self.stdout.write("  - If private: ECR api/dkr, logs, S3 endpoints required.")

        self._output(errors, warnings)

    def _print_manual_network(self):
        self.stdout.write("DEPENDS ON MANUAL AWS CONSOLE CONFIG:")
        self.stdout.write("  - Create Batch compute environment with subnets and security groups (see scripts/infra/batch_video_setup.ps1).")
        self.stdout.write("  - If using private subnets: add NAT Gateway route or VPC endpoints (ecr.api, ecr.dkr, logs, s3).")

    def _output(self, errors, warnings):
        for w in warnings:
            self.stdout.write(self.style.WARNING(w))
        for e in errors:
            self.stdout.write(self.style.ERROR(e))
        if not errors:
            self.stdout.write(self.style.SUCCESS("validate_video_network_requirements: OK (or see warnings)"))
