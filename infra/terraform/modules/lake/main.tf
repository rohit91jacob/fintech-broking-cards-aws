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

variable "glue_prefix" {
  description = "Glue database name prefix (no hyphens)."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for bucket encryption."
  type        = string
}

locals {
  buckets = {
    landing = { purpose = "Firehose error output and raw landing", expire_days = 30 }
    lake    = { purpose = "Iceberg warehouse (raw, ledger, marts)", expire_days = 0 }
    athena  = { purpose = "Athena query results", expire_days = 7 }
  }
}

# ------------------------------------------------------------------ access-log bucket

resource "aws_s3_bucket" "logs" {
  #checkov:skip=CKV_AWS_18:This is the access-log target bucket; logging it to itself would loop.
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for a single-region dev stack.
  #checkov:skip=CKV2_AWS_62:No consumer for event notifications on access logs.
  bucket        = "${var.name}-access-logs"
  force_destroy = false
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    id     = "expire-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ------------------------------------------------------------------- data buckets

resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for a single-region dev stack.
  #checkov:skip=CKV2_AWS_62:Nothing subscribes to object events; Firehose and Athena write directly.
  for_each      = local.buckets
  bucket        = "${var.name}-${each.key}"
  force_destroy = false
  tags = {
    Purpose = each.value.purpose
  }
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = aws_s3_bucket.this[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_logging" "this" {
  for_each      = local.buckets
  bucket        = aws_s3_bucket.this[each.key].id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "${each.key}/"
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  rule {
    id     = "housekeeping"
    status = "Enabled"
    filter {}
    dynamic "expiration" {
      for_each = each.value.expire_days > 0 ? [each.value.expire_days] : []
      content {
        days = expiration.value
      }
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Iceberg data files are immutable; old snapshots are expired by table maintenance, and
  # files untouched for 90 days move to Intelligent-Tiering.
  dynamic "rule" {
    for_each = each.key == "lake" ? [1] : []
    content {
      id     = "tiering"
      status = "Enabled"
      filter {}
      transition {
        days          = 90
        storage_class = "INTELLIGENT_TIERING"
      }
      abort_incomplete_multipart_upload {
        days_after_initiation = 7
      }
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = local.buckets
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.this[each.key].arn,
      "${aws_s3_bucket.this[each.key].arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  policy   = data.aws_iam_policy_document.tls_only[each.key].json
}

# ------------------------------------------------------------------ Glue catalog

resource "aws_glue_catalog_database" "this" {
  for_each    = toset(["lake", "staging", "intermediate", "marts"])
  name        = "${var.glue_prefix}_${each.key}"
  description = each.key == "lake" ? "Iceberg tables written by Firehose and the ingest jobs" : "dbt ${each.key} models (Athena target)"
}

resource "aws_glue_data_catalog_encryption_settings" "this" {
  data_catalog_encryption_settings {
    connection_password_encryption {
      return_connection_password_encrypted = true
      aws_kms_key_id                       = var.kms_key_arn
    }
    encryption_at_rest {
      catalog_encryption_mode = "SSE-KMS"
      sse_aws_kms_key_id      = var.kms_key_arn
    }
  }
}

locals {
  iceberg_tables = {
    ledger_entries = [
      ["txn_id", "string"], ["leg_no", "int"], ["txn_type", "string"], ["status", "string"],
      ["business_date", "date"], ["event_ts", "timestamp"], ["account", "string"], ["unit", "string"],
      ["amount", "decimal(20,4)"], ["idempotency_key", "string"], ["stream_sequence", "string"],
      ["simulated", "boolean"],
    ]
    business_events = [
      ["txn_id", "string"], ["txn_type", "string"], ["status", "string"], ["reason", "string"],
      ["business_date", "date"], ["event_ts", "timestamp"], ["idempotency_key", "string"],
      ["customer_id", "string"], ["card_id", "string"], ["symbol", "string"], ["isin", "string"],
      ["side", "string"], ["qty", "decimal(20,4)"], ["price", "decimal(20,4)"],
      ["notional", "decimal(20,4)"], ["fees", "decimal(20,4)"], ["amount", "decimal(20,4)"],
      ["settlement_date", "date"], ["trade_txn_id", "string"], ["trade_date", "date"],
      ["channel", "string"], ["merchant_category", "string"], ["mcc", "string"],
      ["merchant_city", "string"], ["injected_fraud", "boolean"], ["fraud_scenario", "string"],
      ["stream_sequence", "string"], ["simulated", "boolean"],
    ]
  }
}

# Firehose's Iceberg destination writes into existing Glue tables.
resource "aws_glue_catalog_table" "iceberg" {
  for_each      = local.iceberg_tables
  name          = each.key
  database_name = aws_glue_catalog_database.this["lake"].name
  table_type    = "EXTERNAL_TABLE"

  open_table_format_input {
    iceberg_input {
      metadata_operation = "CREATE"
      version            = "2"
    }
  }

  storage_descriptor {
    location = "s3://${aws_s3_bucket.this["lake"].bucket}/warehouse/${each.key}/"
    dynamic "columns" {
      for_each = each.value
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

output "lake_bucket" {
  description = "Iceberg warehouse bucket."
  value       = aws_s3_bucket.this["lake"].bucket
}

output "lake_bucket_arn" {
  description = "Iceberg warehouse bucket ARN."
  value       = aws_s3_bucket.this["lake"].arn
}

output "landing_bucket_arn" {
  description = "Landing / Firehose error bucket ARN."
  value       = aws_s3_bucket.this["landing"].arn
}

output "athena_results_bucket" {
  description = "Athena results bucket."
  value       = aws_s3_bucket.this["athena"].bucket
}

output "lake_database" {
  description = "Glue database with the Iceberg tables."
  value       = aws_glue_catalog_database.this["lake"].name
}

output "glue_databases" {
  description = "All Glue databases."
  value       = { for k, v in aws_glue_catalog_database.this : k => v.name }
}

output "ledger_entries_table" {
  description = "Glue table for ledger entries."
  value       = aws_glue_catalog_table.iceberg["ledger_entries"].name
}

output "business_events_table" {
  description = "Glue table for business events."
  value       = aws_glue_catalog_table.iceberg["business_events"].name
}
