locals {
  name = "${var.project}-${var.environment}"
  # Glue/Athena names cannot contain hyphens.
  glue_prefix = replace(local.name, "-", "_")

  tags = {
    Project     = "fintech-broking-cards-aws"
    Environment = var.environment
    Owner       = var.owner
    ManagedBy   = "terraform"
    DataClass   = "simulated-and-public"
  }
}

module "kms" {
  source     = "./modules/kms"
  name       = local.name
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = var.region
}

module "ledger" {
  source      = "./modules/ledger"
  name        = local.name
  kms_key_arn = module.kms.key_arn
  environment = var.environment
}

module "lake" {
  source      = "./modules/lake"
  name        = local.name
  glue_prefix = local.glue_prefix
  kms_key_arn = module.kms.key_arn
}

module "streaming" {
  source             = "./modules/streaming"
  name               = local.name
  kms_key_arn        = module.kms.key_arn
  lake_bucket_arn    = module.lake.lake_bucket_arn
  errors_bucket_arn  = module.lake.landing_bucket_arn
  glue_database      = module.lake.lake_database
  ledger_table_name  = module.lake.ledger_entries_table
  events_table_name  = module.lake.business_events_table
  account_id         = data.aws_caller_identity.current.account_id
  partition          = data.aws_partition.current.partition
  region             = var.region
  log_retention_days = var.log_retention_days
}

module "compute" {
  source                = "./modules/compute"
  name                  = local.name
  kms_key_arn           = module.kms.key_arn
  lambda_package_path   = var.lambda_package_path
  journal_table_arn     = module.ledger.journal_table_arn
  journal_stream_arn    = module.ledger.journal_stream_arn
  balances_table_arn    = module.ledger.balances_table_arn
  idempotency_table_arn = module.ledger.idempotency_table_arn
  firehose_stream_arns  = module.streaming.delivery_stream_arns
  resource_prefix       = local.name
  log_retention_days    = var.log_retention_days
}

module "analytics" {
  source                      = "./modules/analytics"
  name                        = local.name
  kms_key_arn                 = module.kms.key_arn
  results_bucket_name         = module.lake.athena_results_bucket
  athena_bytes_scanned_cutoff = var.athena_bytes_scanned_cutoff
}

module "guardrails" {
  source                = "./modules/guardrails"
  name                  = local.name
  kms_key_arn           = module.kms.key_arn
  monthly_budget_usd    = var.monthly_budget_usd
  alert_email           = var.alert_email
  stream_lambda_name    = module.compute.stream_function_name
  api_lambda_name       = module.compute.api_function_name
  delivery_stream_names = module.streaming.delivery_stream_names
  journal_table_name    = module.ledger.journal_table_name
  balances_table_name   = module.ledger.balances_table_name
  dlq_name              = module.compute.dlq_name
}

module "ci_oidc" {
  source              = "./modules/ci_oidc"
  count               = var.github_repository == "" ? 0 : 1
  name                = local.name
  github_repository   = var.github_repository
  create_provider     = var.create_github_oidc_provider
  account_id          = data.aws_caller_identity.current.account_id
  partition           = data.aws_partition.current.partition
  state_resource_arns = [module.lake.lake_bucket_arn]
}
