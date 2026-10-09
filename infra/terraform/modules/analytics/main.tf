terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }
}

variable "name" {
  description = "Name prefix."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for query results."
  type        = string
}

variable "results_bucket_name" {
  description = "Bucket for Athena query results."
  type        = string
}

variable "athena_bytes_scanned_cutoff" {
  description = "Per-query scan limit in bytes."
  type        = number
}

# dbt (target athena) runs in this workgroup. Settings are enforced, results encrypted, and
# every query is capped so a runaway model cannot scan the whole lake.
resource "aws_athena_workgroup" "analytics" {
  name          = "${var.name}-analytics"
  force_destroy = false

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.athena_bytes_scanned_cutoff

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "s3://${var.results_bucket_name}/results/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = var.kms_key_arn
      }
    }
  }
}

output "workgroup" {
  description = "Athena workgroup name."
  value       = aws_athena_workgroup.analytics.name
}
