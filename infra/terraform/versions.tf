terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }

  # Remote state for a real deployment (S3 native locking, Terraform >= 1.10). It stays
  # commented out so `terraform init -backend=false` / validate work without any account.
  # backend "s3" {
  #   bucket       = "<state-bucket>"
  #   key          = "fintech-broking-cards-aws/dev.tfstate"
  #   region       = "ap-south-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}
