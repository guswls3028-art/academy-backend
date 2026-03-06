# VPC — Use existing academy-v1-vpc (do not create new)
# Subnet IDs from terraform.tfvars (vars from params.yaml / scripts/v1)

data "aws_vpc" "main" {
  id = var.vpc_id
}
