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
  description = "KMS key for the SNS topic."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly budget in USD."
  type        = number
}

variable "alert_email" {
  description = "E-mail for notifications (empty = none)."
  type        = string
}

variable "stream_lambda_name" {
  description = "Stream processor function."
  type        = string
}

variable "api_lambda_name" {
  description = "API function."
  type        = string
}

variable "delivery_stream_names" {
  description = "Firehose streams."
  type        = list(string)
}

variable "journal_table_name" {
  description = "Journal table."
  type        = string
}

variable "balances_table_name" {
  description = "Balances table."
  type        = string
}

variable "dlq_name" {
  description = "Dead-letter queue."
  type        = string
}

resource "aws_sns_topic" "alerts" {
  name              = "${var.name}-alerts"
  kms_master_key_id = var.kms_key_arn
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Cost guardrail: e-mail at 50 / 80 / 100 % of actual spend and 100 % of forecast, scoped to
# this project's cost-allocation tag.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$fintech-broking-cards-aws"]
  }

  dynamic "notification" {
    for_each = { actual50 = ["ACTUAL", 50], actual80 = ["ACTUAL", 80], actual100 = ["ACTUAL", 100], forecast100 = ["FORECASTED", 100] }
    content {
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value[1]
      threshold_type            = "PERCENTAGE"
      notification_type         = notification.value[0]
      subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
    }
  }
}

locals {
  alarms = {
    stream_errors = {
      namespace = "AWS/Lambda", metric = "Errors", stat = "Sum", threshold = 0
      dims      = { FunctionName = var.stream_lambda_name }
      what      = "Stream processor errors"
    }
    stream_iterator_age = {
      namespace = "AWS/Lambda", metric = "IteratorAge", stat = "Maximum", threshold = 300000
      dims      = { FunctionName = var.stream_lambda_name }
      what      = "Ledger events more than 5 minutes behind"
    }
    api_errors = {
      namespace = "AWS/Lambda", metric = "Errors", stat = "Sum", threshold = 5
      dims      = { FunctionName = var.api_lambda_name }
      what      = "Ledger API errors"
    }
    journal_throttles = {
      namespace = "AWS/DynamoDB", metric = "ThrottledRequests", stat = "Sum", threshold = 0
      dims      = { TableName = var.journal_table_name }
      what      = "Journal table throttling"
    }
    balances_condition_failures = {
      namespace = "AWS/DynamoDB", metric = "TransactionConflict", stat = "Sum", threshold = 50
      dims      = { TableName = var.balances_table_name }
      what      = "Hot balance items (transaction conflicts)"
    }
    dlq_depth = {
      namespace = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", stat = "Maximum", threshold = 0
      dims      = { QueueName = var.dlq_name }
      what      = "Undeliverable ledger events in the DLQ"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each            = local.alarms
  alarm_name          = "${var.name}-${replace(each.key, "_", "-")}"
  alarm_description   = each.value.what
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  dimensions          = each.value.dims
  period              = 300
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "firehose_freshness" {
  for_each            = toset(var.delivery_stream_names)
  alarm_name          = "${each.value}-freshness"
  alarm_description   = "Firehose delivery to Iceberg is more than 15 minutes behind"
  namespace           = "AWS/Firehose"
  metric_name         = "DeliveryToIceberg.DataFreshness"
  statistic           = "Maximum"
  dimensions          = { DeliveryStreamName = each.value }
  period              = 300
  evaluation_periods  = 2
  threshold           = 900
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
