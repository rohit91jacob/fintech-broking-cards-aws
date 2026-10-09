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
  description = "KMS key for server-side encryption."
  type        = string
}

variable "environment" {
  description = "Environment; prod tables get deletion protection."
  type        = string
}

locals {
  protect = var.environment == "prod"
}

# Append-only journal: one header item (sk = "H") and one item per leg (sk = "L#001"...).
# Its stream (NEW_IMAGE) drives the Lambda that ships entries to Firehose.
resource "aws_dynamodb_table" "journal" {
  name                        = "${var.name}-ledger-journal"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "txn_id"
  range_key                   = "sk"
  stream_enabled              = true
  stream_view_type            = "NEW_IMAGE"
  deletion_protection_enabled = local.protect

  attribute {
    name = "txn_id"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}

# Current balance per (account, unit), updated atomically with the journal writes.
resource "aws_dynamodb_table" "balances" {
  name                        = "${var.name}-ledger-balances"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "account"
  range_key                   = "unit"
  deletion_protection_enabled = local.protect

  attribute {
    name = "account"
    type = "S"
  }
  attribute {
    name = "unit"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}

# Idempotency keys, written in the same transaction with attribute_not_exists; expire after 30 days.
resource "aws_dynamodb_table" "idempotency" {
  name                        = "${var.name}-ledger-idempotency"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "idempotency_key"
  deletion_protection_enabled = local.protect

  attribute {
    name = "idempotency_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}

output "journal_table_name" {
  description = "Journal table name."
  value       = aws_dynamodb_table.journal.name
}

output "journal_table_arn" {
  description = "Journal table ARN."
  value       = aws_dynamodb_table.journal.arn
}

output "journal_stream_arn" {
  description = "Journal stream ARN."
  value       = aws_dynamodb_table.journal.stream_arn
}

output "balances_table_name" {
  description = "Balances table name."
  value       = aws_dynamodb_table.balances.name
}

output "balances_table_arn" {
  description = "Balances table ARN."
  value       = aws_dynamodb_table.balances.arn
}

output "idempotency_table_arn" {
  description = "Idempotency table ARN."
  value       = aws_dynamodb_table.idempotency.arn
}
