variable "project" {
  description = "Project name, used in resource names and tags."
  type        = string
  default     = "fbc"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,10}$", var.project))
    error_message = "project must be 2-11 lowercase letters, digits or hyphens."
  }
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "region" {
  description = "AWS region. ap-south-1 (Mumbai) keeps Indian customer data in India."
  type        = string
  default     = "ap-south-1"
}

variable "owner" {
  description = "Owner tag value."
  type        = string
  default     = "rohit91jacob"
}

variable "lambda_package_path" {
  description = "Path of the zip with the fbc package and its dependencies (built by `make lambda-zip`)."
  type        = string
  default     = "../../build/lambda.zip"
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget; alerts at 50/80/100 % actual and 100 % forecast."
  type        = number
  default     = 25

  validation {
    condition     = var.monthly_budget_usd > 0 && var.monthly_budget_usd <= 1000
    error_message = "monthly_budget_usd must be between 1 and 1000."
  }
}

variable "alert_email" {
  description = "Address subscribed to budget and alarm notifications (empty = no subscription)."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 365
}

variable "athena_bytes_scanned_cutoff" {
  description = "Per-query scan limit for the Athena workgroup (cost guardrail)."
  type        = number
  default     = 1073741824 # 1 GiB
}

variable "github_repository" {
  description = "owner/repo allowed to assume the deploy role through GitHub OIDC (empty = no role)."
  type        = string
  default     = ""
}

variable "tf_state_bucket" {
  description = "Existing S3 bucket for the Terraform state (the deploy role gets access to the fintech-broking-cards-aws/ key prefix only)."
  type        = string
  default     = ""
}

variable "create_github_oidc_provider" {
  description = "Create the account's GitHub OIDC identity provider (only one may exist per account)."
  type        = bool
  default     = false
}
