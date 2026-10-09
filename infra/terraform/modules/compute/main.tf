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

variable "lambda_package_path" {
  description = "Zip with the fbc package and dependencies."
  type        = string
}

variable "journal_table_arn" {
  description = "Journal table ARN."
  type        = string
}

variable "journal_stream_arn" {
  description = "Journal stream ARN."
  type        = string
}

variable "balances_table_arn" {
  description = "Balances table ARN."
  type        = string
}

variable "idempotency_table_arn" {
  description = "Idempotency table ARN."
  type        = string
}

variable "firehose_stream_arns" {
  description = "Firehose streams the stream processor may write to."
  type        = list(string)
}

variable "resource_prefix" {
  description = "FBC_RESOURCE_PREFIX passed to the functions (table and stream names)."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
}

locals {
  functions = {
    api = {
      handler     = "fbc.lambdas.api_handler"
      memory      = 512
      timeout     = 10
      concurrency = 20
    }
    stream = {
      handler     = "fbc.lambdas.stream_handler"
      memory      = 512
      timeout     = 60
      concurrency = 5
    }
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.functions
  name               = "${var.name}-${each.key}-lambda"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-stream-dlq"
  kms_master_key_id         = var.kms_key_arn
  message_retention_seconds = 1209600
}

# The API may only touch the three ledger tables, and only with the calls it makes.
data "aws_iam_policy_document" "api" {
  statement {
    sid       = "LedgerWrites"
    actions   = ["dynamodb:TransactWriteItems", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:ConditionCheckItem"]
    resources = [var.journal_table_arn, var.balances_table_arn, var.idempotency_table_arn]
  }
  statement {
    sid       = "LedgerReads"
    actions   = ["dynamodb:GetItem"]
    resources = [var.balances_table_arn, var.idempotency_table_arn]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn["api"].arn}:*"]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray does not support resource-level permissions
  }
}

# The stream processor reads the journal stream and writes to the two Firehose streams.
data "aws_iam_policy_document" "stream" {
  statement {
    sid       = "ReadJournalStream"
    actions   = ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:ListStreams"]
    resources = [var.journal_stream_arn]
  }
  statement {
    sid       = "Deliver"
    actions   = ["firehose:PutRecordBatch", "firehose:PutRecord"]
    resources = var.firehose_stream_arns
  }
  statement {
    sid       = "DeadLetter"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn["stream"].arn}:*"]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray does not support resource-level permissions
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "ledger-api"
  role   = aws_iam_role.fn["api"].id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_iam_role_policy" "stream" {
  name   = "stream-to-firehose"
  role   = aws_iam_role.fn["stream"].id
  policy = data.aws_iam_policy_document.stream.json
}

resource "aws_lambda_function" "fn" {
  #checkov:skip=CKV_AWS_117:The functions only call regional AWS APIs; a VPC would need NAT or interface endpoints (cost) and adds no isolation here.
  #checkov:skip=CKV_AWS_272:Code signing needs an AWS Signer profile in the deploy pipeline; the zip is built and hashed in CI instead.
  for_each                       = local.functions
  function_name                  = "${var.name}-${each.key}"
  role                           = aws_iam_role.fn[each.key].arn
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  handler                        = each.value.handler
  filename                       = var.lambda_package_path
  source_code_hash               = filebase64sha256(var.lambda_package_path)
  memory_size                    = each.value.memory
  timeout                        = each.value.timeout
  reserved_concurrent_executions = each.value.concurrency
  kms_key_arn                    = var.kms_key_arn

  environment {
    variables = {
      FBC_RESOURCE_PREFIX = var.resource_prefix
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.fn[each.key].name
  }

  depends_on = [aws_iam_role_policy.api, aws_iam_role_policy.stream]
}

# DynamoDB Streams -> Lambda: partial batch responses, bisect on error, bounded retries,
# failed batches go to the DLQ (on-failure destination) instead of blocking the shard.
resource "aws_lambda_event_source_mapping" "journal" {
  event_source_arn                   = var.journal_stream_arn
  function_name                      = aws_lambda_function.fn["stream"].arn
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 5
  bisect_batch_on_function_error     = true
  maximum_retry_attempts             = 5
  maximum_record_age_in_seconds      = 3600
  function_response_types            = ["ReportBatchItemFailures"]

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.dlq.arn
    }
  }
}

# ------------------------------------------------------------------- HTTP API

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.name}-ledger"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_apigatewayv2_api" "ledger" {
  name          = "${var.name}-ledger"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "ledger" {
  api_id                 = aws_apigatewayv2_api.ledger.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn["api"].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "ledger" {
  for_each = toset([
    "POST /deposits",
    "POST /orders",
    "POST /settlements",
    "POST /card-authorizations",
    "POST /card-payments",
    "GET /balances/{account}/{unit}",
  ])
  api_id             = aws_apigatewayv2_api.ledger.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.ledger.id}"
  authorization_type = "AWS_IAM" # callers sign requests with SigV4; no anonymous access
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.ledger.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      route     = "$context.routeKey"
      status    = "$context.status"
      latency   = "$context.integrationLatency"
      caller    = "$context.identity.caller"
    })
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ledger.execution_arn}/*/*"
}

output "api_endpoint" {
  description = "Ledger API endpoint."
  value       = aws_apigatewayv2_api.ledger.api_endpoint
}

output "stream_function_name" {
  description = "Stream processor function name."
  value       = aws_lambda_function.fn["stream"].function_name
}

output "api_function_name" {
  description = "API function name."
  value       = aws_lambda_function.fn["api"].function_name
}

output "dlq_name" {
  description = "Dead-letter queue name."
  value       = aws_sqs_queue.dlq.name
}
