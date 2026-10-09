output "journal_table" {
  description = "DynamoDB journal table (stream source)."
  value       = module.ledger.journal_table_name
}

output "api_endpoint" {
  description = "HTTP API endpoint of the ledger API (IAM-authorised)."
  value       = module.compute.api_endpoint
}

output "lake_bucket" {
  description = "S3 bucket holding the Iceberg warehouse."
  value       = module.lake.lake_bucket
}

output "glue_databases" {
  description = "Glue databases: lake (Iceberg sources) and dbt staging/intermediate/marts."
  value       = module.lake.glue_databases
}

output "athena_workgroup" {
  description = "Athena workgroup that dbt (target athena) runs in."
  value       = module.analytics.workgroup
}

output "deploy_role_arn" {
  description = "Role for GitHub Actions OIDC (empty when github_repository is unset)."
  value       = try(module.ci_oidc[0].role_arn, "")
}
