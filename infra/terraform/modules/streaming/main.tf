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
  description = "KMS key."
  type        = string
}

variable "lake_bucket_arn" {
  description = "Iceberg warehouse bucket ARN."
  type        = string
}

variable "errors_bucket_arn" {
  description = "Bucket for records Firehose could not deliver."
  type        = string
}

variable "glue_database" {
  description = "Glue database holding the destination Iceberg tables."
  type        = string
}

variable "ledger_table_name" {
  description = "Iceberg table for ledger entries."
  type        = string
}

variable "events_table_name" {
  description = "Iceberg table for business events."
  type        = string
}

variable "account_id" {
  description = "AWS account id."
  type        = string
}

variable "partition" {
  description = "AWS partition."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
}

locals {
  catalog_arn = "arn:${var.partition}:glue:${var.region}:${var.account_id}:catalog"
  db_arn      = "arn:${var.partition}:glue:${var.region}:${var.account_id}:database/${var.glue_database}"
  streams = {
    "ledger-entries" = {
      table       = var.ledger_table_name
      unique_keys = ["txn_id", "leg_no"]
    }
    "business-events" = {
      table       = var.events_table_name
      unique_keys = ["txn_id"]
    }
  }
}

resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/kinesisfirehose/${var.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_cloudwatch_log_stream" "delivery" {
  for_each       = local.streams
  name           = "${each.key}-delivery"
  log_group_name = aws_cloudwatch_log_group.firehose.name
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "${var.name}-firehose"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "firehose" {
  statement {
    sid     = "IcebergCatalog"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:UpdateTable"]
    resources = concat(
      [local.catalog_arn, local.db_arn],
      [for s in local.streams : "arn:${var.partition}:glue:${var.region}:${var.account_id}:table/${var.glue_database}/${s.table}"]
    )
  }
  statement {
    sid = "WarehouseAndErrors"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetBucketLocation",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [
      var.lake_bucket_arn,
      "${var.lake_bucket_arn}/warehouse/*",
      var.errors_bucket_arn,
      "${var.errors_bucket_arn}/firehose-errors/*",
    ]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.firehose.arn}:*"]
  }
}

resource "aws_iam_role_policy" "firehose" {
  name   = "deliver-to-iceberg"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose.json
}

# Direct PUT streams written by the stream-processor Lambda. Firehose writes straight into
# the Glue-registered Iceberg tables and upserts on the unique keys, which de-duplicates the
# at-least-once delivery of DynamoDB Streams.
resource "aws_kinesis_firehose_delivery_stream" "this" {
  for_each    = local.streams
  name        = "${var.name}-${each.key}"
  destination = "iceberg"

  server_side_encryption {
    enabled  = true
    key_type = "CUSTOMER_MANAGED_CMK"
    key_arn  = var.kms_key_arn
  }

  iceberg_configuration {
    role_arn           = aws_iam_role.firehose.arn
    catalog_arn        = local.catalog_arn
    buffering_size     = 64
    buffering_interval = 60
    retry_duration     = 300
    s3_backup_mode     = "FailedDataOnly"

    destination_table_configuration {
      database_name = var.glue_database
      table_name    = each.value.table
      unique_keys   = each.value.unique_keys
    }

    s3_configuration {
      role_arn            = aws_iam_role.firehose.arn
      bucket_arn          = var.errors_bucket_arn
      error_output_prefix = "firehose-errors/${each.key}/"
      kms_key_arn         = var.kms_key_arn
    }

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = aws_cloudwatch_log_stream.delivery[each.key].name
    }
  }
}

output "delivery_stream_arns" {
  description = "Firehose delivery stream ARNs."
  value       = [for s in aws_kinesis_firehose_delivery_stream.this : s.arn]
}

output "delivery_stream_names" {
  description = "Firehose delivery stream names."
  value       = [for s in aws_kinesis_firehose_delivery_stream.this : s.name]
}
