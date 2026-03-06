terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # Configure per environment
    # bucket         = "academy-terraform-state"
    # key            = "infra/terraform.tfstate"
    # region         = "ap-northeast-2"
    # dynamodb_table = "academy-terraform-lock"
  }
}

provider "aws" {
  region = "ap-northeast-2"

  default_tags {
    tags = {
      Project    = "academy"
      ManagedBy  = "terraform"
      SSOT       = "docs/00-SSOT/v1/params.yaml"
    }
  }
}
