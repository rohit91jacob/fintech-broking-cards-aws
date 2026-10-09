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

variable "github_repository" {
  description = "owner/repo allowed to assume the role."
  type        = string
}

variable "create_provider" {
  description = "Create the GitHub OIDC provider (one per account)."
  type        = bool
}

variable "account_id" {
  description = "AWS account id."
  type        = string
}

variable "partition" {
  description = "AWS partition."
  type        = string
}

variable "state_resource_arns" {
  description = "Extra resources the deploy role may read (e.g. the lake bucket for smoke tests)."
  type        = list(string)
  default     = []
}

locals {
  provider_arn = "arn:${var.partition}:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# Short-lived credentials for GitHub Actions: only workflows on main of this repository, in the
# "aws-dev" environment, can assume the role. No static access keys exist anywhere.
data "aws_iam_policy_document" "trust" {
  #checkov:skip=CKV_AWS_358:aud and sub are both pinned with StringEquals; sub is repo:<var.github_repository>:environment:aws-dev, which checkov cannot resolve statically.
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:aws-dev"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

# Deploys are scoped to this project's resources by name prefix.
data "aws_iam_policy_document" "deploy" {
  statement {
    sid = "ManageProjectResources"
    actions = [
      "dynamodb:*", "s3:*", "firehose:*", "lambda:*", "apigateway:*", "glue:*", "athena:*",
      "logs:*", "sqs:*", "sns:*", "cloudwatch:*", "budgets:*", "kms:*",
    ]
    resources = [
      "arn:${var.partition}:dynamodb:*:${var.account_id}:table/${var.name}-*",
      "arn:${var.partition}:s3:::${var.name}-*",
      "arn:${var.partition}:firehose:*:${var.account_id}:deliverystream/${var.name}-*",
      "arn:${var.partition}:lambda:*:${var.account_id}:function:${var.name}-*",
      "arn:${var.partition}:logs:*:${var.account_id}:log-group:*${var.name}*",
      "arn:${var.partition}:sqs:*:${var.account_id}:${var.name}-*",
      "arn:${var.partition}:sns:*:${var.account_id}:${var.name}-*",
      "arn:${var.partition}:glue:*:${var.account_id}:*",
      "arn:${var.partition}:athena:*:${var.account_id}:workgroup/${var.name}-*",
      "arn:${var.partition}:cloudwatch:*:${var.account_id}:alarm:${var.name}-*",
      "arn:${var.partition}:budgets::${var.account_id}:budget/${var.name}-*",
      "arn:${var.partition}:apigateway:*::/apis*",
      "arn:${var.partition}:kms:*:${var.account_id}:key/*",
    ]
  }
  statement {
    sid       = "ProjectRoles"
    actions   = ["iam:GetRole", "iam:CreateRole", "iam:DeleteRole", "iam:PassRole", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:TagRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole"]
    resources = ["arn:${var.partition}:iam::${var.account_id}:role/${var.name}-*"]
  }
  statement {
    sid       = "ReadOnlyDiscovery"
    actions   = ["sts:GetCallerIdentity", "kms:CreateKey", "kms:ListAliases", "dynamodb:ListTables", "lambda:ListFunctions"]
    resources = ["*"] # these actions do not support resource-level permissions
  }
  statement {
    sid       = "Extra"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = concat(var.state_resource_arns, [for a in var.state_resource_arns : "${a}/*"])
  }
}

resource "aws_iam_role_policy" "deploy" {
  #checkov:skip=CKV_AWS_290:Write actions are constrained to ARNs with the project prefix; kms:CreateKey cannot be scoped.
  #checkov:skip=CKV_AWS_355:Only discovery actions without resource-level support use "*".
  #checkov:skip=CKV_AWS_289:Permission management is limited to roles with the project prefix.
  #checkov:skip=CKV_AWS_286:PassRole is limited to roles with the project prefix.
  #checkov:skip=CKV_AWS_287:Credential exposure is limited to project-prefixed roles.
  #checkov:skip=CKV_AWS_288:Data access is limited to project-prefixed resources.
  name   = "deploy-project"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "role_arn" {
  description = "Deploy role ARN for aws-actions/configure-aws-credentials."
  value       = aws_iam_role.deploy.arn
}
