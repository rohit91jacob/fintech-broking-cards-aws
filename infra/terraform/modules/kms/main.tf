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

# One customer-managed key for every data store in the stack (DynamoDB, S3, Firehose,
# Lambda environment, SQS, SNS, CloudWatch Logs, Athena results).
data "aws_iam_policy_document" "key" {
  #checkov:skip=CKV_AWS_109:Key policy; "*" refers to this key only and the root principal is the standard account-delegation statement.
  #checkov:skip=CKV_AWS_111:Key policy; "*" refers to this key only.
  #checkov:skip=CKV_AWS_356:Key policy; resources must be "*" (meaning this key) in a KMS key policy.
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"] # "*" in a key policy means "this key"
    principals {
      type        = "AWS"
      identifiers = ["arn:${var.partition}:iam::${var.account_id}:root"]
    }
  }

  statement {
    sid = "AllowServicesInThisAccount"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = ["*"]
    principals {
      type = "Service"
      identifiers = [
        "logs.${var.region}.amazonaws.com",
        "sns.amazonaws.com",
        "sqs.amazonaws.com",
        "firehose.amazonaws.com",
        "budgets.amazonaws.com",
        "cloudwatch.amazonaws.com",
      ]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_kms_key" "this" {
  description             = "${var.name} data encryption"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.key.json
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}-data"
  target_key_id = aws_kms_key.this.key_id
}

output "key_arn" {
  description = "ARN of the data key."
  value       = aws_kms_key.this.arn
}
