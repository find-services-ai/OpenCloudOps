"""Terraform / OpenTofu backend.

Renders HCL that a reviewer can read and `terraform plan` can consume. Choices
worth knowing about:

* Names come from ``ResolvedNode.name``, never from an inline expression, so
  what you read in the file is what will exist in the account.
* Tags come from one ``local.tags`` map merged per resource, so a tagging
  change is one diff rather than one per resource.
* Every IAM policy enumerates its actions and scopes ``Resource`` to the ARNs
  the node owns. The learner's ``no-wildcard-iam`` lesson fails the build
  otherwise, which is the point: the check and the generator agree.
"""

from __future__ import annotations

import json

from opencloudops.emitters.base import Emitter
from opencloudops.naming import tf_identifier
from opencloudops.spec import NodeType, PlatformContract, ResolvedNode, ResolvedPlan


def _hcl_value(value: object, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        inner = ", ".join(_hcl_value(v) for v in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        lines = ["{"]
        for k, v in value.items():
            lines.append(f'{pad}  {json.dumps(k)} = {_hcl_value(v, indent + 1)}')
        lines.append(pad + "}")
        return "\n".join(lines)
    return json.dumps(str(value))


def _tags_block(node: ResolvedNode, indent: str = "  ") -> str:
    extras = {k: v for k, v in node.tags.items() if k == "Component"}
    if extras:
        pairs = ", ".join(f'{k} = "{v}"' for k, v in extras.items())
        return f"{indent}tags = merge(local.tags, {{ {pairs} }})"
    return f"{indent}tags = local.tags"


class TerraformEmitter(Emitter):
    id = "terraform"
    language = "Terraform"
    description = "HCL for Terraform >= 1.6 or OpenTofu. Also the OpenTofu target."

    def emit(self, plan: ResolvedPlan, platform: PlatformContract) -> dict[str, str]:
        files: dict[str, str] = {
            "versions.tf": self._versions(plan),
            "variables.tf": self._variables(plan),
            "locals.tf": self._locals(plan, platform),
            "main.tf": self._main(plan),
            "outputs.tf": self._outputs(plan),
            f"tfvars/{plan.environment}.tfvars": self._tfvars(plan),
        }
        files.update(self.skip_note(plan))
        return files

    # -- scaffolding ----------------------------------------------------

    def _versions(self, plan: ResolvedPlan) -> str:
        return self.header(plan) + """terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}
"""

    def _variables(self, plan: ResolvedPlan) -> str:
        body = [
            self.header(plan),
            'variable "region" {',
            "  description = \"Region this environment deploys into.\"",
            "  type        = string",
            f'  default     = "{plan.region}"',
            "}",
            "",
            'variable "environment" {',
            "  description = \"Environment name. One variable, never two -- a second env",
            "  variable is how cross-environment leaks stop being detectable.\"",
            "  type        = string",
            f'  default     = "{plan.environment}"',
            "}",
            "",
            'variable "alarm_email" {',
            '  description = "Where SLO alarms are delivered. Empty disables the subscription."',
            "  type        = string",
            '  default     = ""',
            "}",
            "",
            'variable "vpc_private_subnet_ids" {',
            '  description = "Existing private subnets, when this plan does not create a VPC."',
            "  type        = list(string)",
            "  default     = []",
            "}",
        ]
        for node in plan.by_type(NodeType.container_service):
            if not node.params.get("image"):
                body += [
                    "",
                    f'variable "{tf_identifier(node.id)}_image" {{',
                    f'  description = "Container image URI:tag for {node.id}."',
                    "  type        = string",
                    "}",
                ]
        return "\n".join(body) + "\n"

    def _locals(self, plan: ResolvedPlan, platform: PlatformContract) -> str:
        tags = dict(sorted(plan.nodes[0].tags.items())) if plan.nodes else {}
        tags.pop("Component", None)
        lines = [
            self.header(plan),
            "locals {",
            "  tags = " + _hcl_value(tags, 1),
            "",
            f'  service = "{plan.service}"',
            f'  min_tls_version = "{platform.security.min_tls_version}"',
            "}",
        ]
        return "\n".join(lines) + "\n"

    def _tfvars(self, plan: ResolvedPlan) -> str:
        lines = [
            self.header(plan),
            f'region      = "{plan.region}"',
            f'environment = "{plan.environment}"',
            'alarm_email = ""',
        ]
        for node in plan.by_type(NodeType.container_service):
            if not node.params.get("image"):
                lines.append(
                    f'{tf_identifier(node.id)}_image = "REPLACE-WITH-REGISTRY-URI:TAG"'
                )
        lines += [
            "",
            "# Environment-specific values live here and only here. Copying a value",
            "# from another environment's tfvars is the leak the validator blocks.",
        ]
        return "\n".join(lines) + "\n"

    def _outputs(self, plan: ResolvedPlan) -> str:
        lines = [self.header(plan)]
        for node in self.supported_nodes(plan):
            ident = tf_identifier(node.id)
            ref = self._primary_ref(node)
            if ref is None:
                continue
            lines += [
                f'output "{ident}_name" {{',
                f'  description = "Resolved name of {node.id} ({node.type.value})."',
                f"  value       = {ref}",
                "}",
                "",
            ]
        return "\n".join(lines)

    def _primary_ref(self, node: ResolvedNode) -> str | None:
        ident = tf_identifier(node.id)
        return {
            NodeType.network: f"aws_vpc.{ident}.id",
            NodeType.function: f"aws_lambda_function.{ident}.arn",
            NodeType.container_service: f"aws_ecs_service.{ident}.name",
            NodeType.object_store: f"aws_s3_bucket.{ident}.bucket",
            NodeType.table: f"aws_dynamodb_table.{ident}.name",
            NodeType.relational: f"aws_db_instance.{ident}.address",
            NodeType.queue: f"aws_sqs_queue.{ident}.url",
            NodeType.secret: f"aws_secretsmanager_secret.{ident}.arn",
            NodeType.observability: f"aws_cloudwatch_log_group.{ident}.name",
        }.get(node.type)

    # -- resources ------------------------------------------------------

    def _main(self, plan: ResolvedPlan) -> str:
        parts = [self.header(plan)]
        renderers = {
            NodeType.network: self._vpc,
            NodeType.function: self._lambda,
            NodeType.container_service: self._ecs,
            NodeType.object_store: self._s3,
            NodeType.table: self._dynamodb,
            NodeType.relational: self._rds,
            NodeType.queue: self._sqs,
            NodeType.secret: self._secret,
            NodeType.observability: self._observability,
        }
        for node in self.supported_nodes(plan):
            render = renderers.get(node.type)
            if render is None:
                continue
            parts.append(f"# {node.id}: {node.type.value} via pattern {node.pattern_id}")
            parts.append(render(node, plan))
        return "\n".join(parts)

    def _kms(self, node: ResolvedNode, purpose: str) -> str:
        ident = tf_identifier(node.id)
        return f'''resource "aws_kms_key" "{ident}" {{
  description             = "CMK for {node.id} ({purpose})"
  enable_key_rotation     = true
  deletion_window_in_days = 30
{_tags_block(node)}
}}

resource "aws_kms_alias" "{ident}" {{
  name          = "alias/{node.name}"
  target_key_id = aws_kms_key.{ident}.key_id
}}
'''

    def _vpc(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        azs = int(node.params.get("availability_zones", 2))
        single_nat = bool(node.params.get("single_nat_gateway", False))
        nat_count = 1 if single_nat else azs
        return f'''resource "aws_vpc" "{ident}" {{
  cidr_block           = {json.dumps(node.params.get("cidr", "10.0.0.0/16"))}
  enable_dns_support   = true
  enable_dns_hostnames = true
{_tags_block(node)}
}}

data "aws_availability_zones" "{ident}" {{
  state = "available"
}}

resource "aws_subnet" "{ident}_public" {{
  count                   = {azs}
  vpc_id                  = aws_vpc.{ident}.id
  availability_zone       = data.aws_availability_zones.{ident}.names[count.index]
  cidr_block              = cidrsubnet(aws_vpc.{ident}.cidr_block, 8, count.index)
  map_public_ip_on_launch = false
  tags                    = merge(local.tags, {{ Component = "{node.id}", Tier = "public" }})
}}

resource "aws_subnet" "{ident}_private" {{
  count             = {azs}
  vpc_id            = aws_vpc.{ident}.id
  availability_zone = data.aws_availability_zones.{ident}.names[count.index]
  cidr_block        = cidrsubnet(aws_vpc.{ident}.cidr_block, 8, count.index + 10)
  tags              = merge(local.tags, {{ Component = "{node.id}", Tier = "private" }})
}}

resource "aws_subnet" "{ident}_isolated" {{
  count             = {azs}
  vpc_id            = aws_vpc.{ident}.id
  availability_zone = data.aws_availability_zones.{ident}.names[count.index]
  cidr_block        = cidrsubnet(aws_vpc.{ident}.cidr_block, 8, count.index + 20)
  tags              = merge(local.tags, {{ Component = "{node.id}", Tier = "isolated" }})
}}

resource "aws_internet_gateway" "{ident}" {{
  vpc_id = aws_vpc.{ident}.id
{_tags_block(node)}
}}

resource "aws_eip" "{ident}_nat" {{
  count  = {nat_count}
  domain = "vpc"
{_tags_block(node)}
}}

resource "aws_nat_gateway" "{ident}" {{
  count         = {nat_count}
  allocation_id = aws_eip.{ident}_nat[count.index].id
  subnet_id     = aws_subnet.{ident}_public[count.index].id
  depends_on    = [aws_internet_gateway.{ident}]
{_tags_block(node)}
}}

resource "aws_cloudwatch_log_group" "{ident}_flow" {{
  name              = "/aws/vpc/{node.name}/flow-logs"
  retention_in_days = 90
{_tags_block(node)}
}}

resource "aws_iam_role" "{ident}_flow" {{
  name = "{node.name}-flow-logs"
  assume_role_policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect    = "Allow"
      Principal = {{ Service = "vpc-flow-logs.amazonaws.com" }}
      Action    = "sts:AssumeRole"
    }}]
  }})
{_tags_block(node)}
}}

resource "aws_iam_role_policy" "{ident}_flow" {{
  name = "{node.name}-flow-logs"
  role = aws_iam_role.{ident}_flow.id
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams",
      ]
      Resource = "${{aws_cloudwatch_log_group.{ident}_flow.arn}}:*"
    }}]
  }})
}}

resource "aws_flow_log" "{ident}" {{
  vpc_id               = aws_vpc.{ident}.id
  traffic_type         = "ALL"
  iam_role_arn         = aws_iam_role.{ident}_flow.arn
  log_destination      = aws_cloudwatch_log_group.{ident}_flow.arn
  max_aggregation_interval = 60
{_tags_block(node)}
}}

resource "aws_default_security_group" "{ident}" {{
  vpc_id = aws_vpc.{ident}.id
  tags   = merge(local.tags, {{ Component = "{node.id}", Note = "intentionally empty" }})
}}
'''

    def _s3(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        versioning = "Enabled" if node.params.get("versioning", True) else "Suspended"
        expire_days = int(node.params.get("lifecycle_expire_days", 0))
        lifecycle = ""
        if expire_days > 0:
            lifecycle = f'''
resource "aws_s3_bucket_lifecycle_configuration" "{ident}" {{
  bucket = aws_s3_bucket.{ident}.id

  rule {{
    id     = "expire"
    status = "Enabled"

    filter {{}}

    expiration {{
      days = {expire_days}
    }}

    noncurrent_version_expiration {{
      noncurrent_days = 30
    }}
  }}
}}
'''
        return self._kms(node, "object encryption") + f'''
resource "aws_s3_bucket" "{ident}" {{
  bucket = "{node.name}"
{_tags_block(node)}
}}

resource "aws_s3_bucket_public_access_block" "{ident}" {{
  bucket                  = aws_s3_bucket.{ident}.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}}

resource "aws_s3_bucket_versioning" "{ident}" {{
  bucket = aws_s3_bucket.{ident}.id

  versioning_configuration {{
    status = "{versioning}"
  }}
}}

resource "aws_s3_bucket_server_side_encryption_configuration" "{ident}" {{
  bucket = aws_s3_bucket.{ident}.id

  rule {{
    apply_server_side_encryption_by_default {{
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.{ident}.arn
    }}
    bucket_key_enabled = true
  }}
}}

resource "aws_s3_bucket_policy" "{ident}" {{
  bucket = aws_s3_bucket.{ident}.id
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Sid       = "DenyPlaintextTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.{ident}.arn,
        "${{aws_s3_bucket.{ident}.arn}}/*",
      ]
      Condition = {{
        Bool = {{ "aws:SecureTransport" = "false" }}
      }}
    }}]
  }})

  depends_on = [aws_s3_bucket_public_access_block.{ident}]
}}
{lifecycle}'''

    def _dynamodb(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        pk = node.params["partition_key"]
        sk = node.params.get("sort_key") or ""
        ttl = node.params.get("ttl_attribute") or ""
        blocks = [
            f'  hash_key = "{pk}"',
            f'''  attribute {{
    name = "{pk}"
    type = "S"
  }}''',
        ]
        if sk:
            blocks.insert(1, f'  range_key = "{sk}"')
            blocks.append(f'''  attribute {{
    name = "{sk}"
    type = "S"
  }}''')
        if ttl:
            blocks.append(f'''  ttl {{
    attribute_name = "{ttl}"
    enabled        = true
  }}''')
        if node.params.get("stream"):
            blocks.append('  stream_enabled   = true')
            blocks.append('  stream_view_type = "NEW_AND_OLD_IMAGES"')
        return self._kms(node, "table encryption") + f'''
resource "aws_dynamodb_table" "{ident}" {{
  name         = "{node.name}"
  billing_mode = "{node.params.get("billing_mode", "PAY_PER_REQUEST")}"
{chr(10).join(blocks)}

  point_in_time_recovery {{
    enabled = true
  }}

  server_side_encryption {{
    enabled     = true
    kms_key_arn = aws_kms_key.{ident}.arn
  }}

  deletion_protection_enabled = {"true" if plan.production else "false"}

{_tags_block(node)}
}}
'''

    def _sqs(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        fifo = bool(node.params.get("fifo", False))
        suffix = ".fifo" if fifo else ""
        return self._kms(node, "queue encryption") + f'''
resource "aws_sqs_queue" "{ident}_dlq" {{
  name                       = "{node.name}-dlq{suffix}"
  fifo_queue                 = {"true" if fifo else "false"}
  kms_master_key_id          = aws_kms_key.{ident}.arn
  message_retention_seconds  = 1209600
  tags                       = merge(local.tags, {{ Component = "{node.id}", Role = "dlq" }})
}}

resource "aws_sqs_queue" "{ident}" {{
  name                       = "{node.name}{suffix}"
  fifo_queue                 = {"true" if fifo else "false"}
  kms_master_key_id          = aws_kms_key.{ident}.arn
  visibility_timeout_seconds = {int(node.params.get("visibility_timeout_seconds", 30))}
  message_retention_seconds  = {int(node.params.get("message_retention_seconds", 345600))}

  redrive_policy = jsonencode({{
    deadLetterTargetArn = aws_sqs_queue.{ident}_dlq.arn
    maxReceiveCount     = {int(node.params.get("max_receive_count", 5))}
  }})

{_tags_block(node)}
}}

resource "aws_sqs_queue_policy" "{ident}" {{
  queue_url = aws_sqs_queue.{ident}.id
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Sid       = "DenyPlaintextTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.{ident}.arn
      Condition = {{
        Bool = {{ "aws:SecureTransport" = "false" }}
      }}
    }}]
  }})
}}
'''

    def _secret(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        return self._kms(node, "secret encryption") + f'''
resource "aws_secretsmanager_secret" "{ident}" {{
  name        = "{node.name}"
  description = {json.dumps(node.params.get("description", "Managed by OpenCloudOps"))}
  kms_key_id  = aws_kms_key.{ident}.arn
{_tags_block(node)}
}}

resource "aws_secretsmanager_secret_version" "{ident}" {{
  secret_id     = aws_secretsmanager_secret.{ident}.id
  secret_string = jsonencode({{ managed_by = "opencloudops" }})

  lifecycle {{
    ignore_changes = [secret_string]
  }}
}}

resource "aws_secretsmanager_secret_rotation" "{ident}" {{
  secret_id           = aws_secretsmanager_secret.{ident}.id
  rotation_lambda_arn = var.rotation_lambda_arn

  rotation_rules {{
    automatically_after_days = {int(node.params.get("rotation_days", 30))}
  }}

  count = var.rotation_lambda_arn == "" ? 0 : 1
}}

variable "rotation_lambda_arn" {{
  description = "Rotation function ARN. Empty leaves rotation unconfigured, which the guard treats as a finding."
  type        = string
  default     = ""
}}
'''

    def _rds(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        vpc_ident = tf_identifier(vpc.id) if vpc else None
        subnet_ids = (
            f"aws_subnet.{vpc_ident}_isolated[*].id" if vpc_ident else "var.vpc_private_subnet_ids"
        )
        vpc_id_ref = f"aws_vpc.{vpc_ident}.id" if vpc_ident else "var.vpc_id"
        vpc_cidr_ref = (
            f"[aws_vpc.{vpc_ident}.cidr_block]" if vpc_ident else "var.vpc_cidr_blocks"
        )
        final_snapshot = f'"{node.name}-final"' if plan.production else "null"
        extra_vars = (
            ""
            if vpc_ident
            else '''
variable "vpc_id" {
  description = "Existing VPC id when this plan does not create one."
  type        = string
}

variable "vpc_cidr_blocks" {
  description = "CIDRs permitted to reach the database."
  type        = list(string)
}
'''
        )
        return self._kms(node, "storage encryption") + f'''
resource "aws_db_subnet_group" "{ident}" {{
  name       = "{node.name}"
  subnet_ids = {subnet_ids}
{_tags_block(node)}
}}

resource "aws_security_group" "{ident}" {{
  name        = "{node.name}"
  description = "Database ingress for {plan.service}"
  vpc_id      = {vpc_id_ref}

  ingress {{
    description = "PostgreSQL from inside the VPC only"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = {vpc_cidr_ref}
  }}

{_tags_block(node)}
}}

resource "aws_db_parameter_group" "{ident}" {{
  name   = "{node.name}"
  family = "postgres16"

  parameter {{
    name  = "rds.force_ssl"
    value = "1"
  }}

{_tags_block(node)}
}}

resource "aws_db_instance" "{ident}" {{
  identifier     = "{node.name}"
  engine         = "postgres"
  engine_version = "{node.params.get("engine_version", "16.4")}"
  instance_class = "{node.params.get("instance_class", "db.t4g.medium")}"

  allocated_storage     = {int(node.params.get("allocated_storage_gb", 50))}
  max_allocated_storage = {int(node.params.get("allocated_storage_gb", 50)) * 4}
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.{ident}.arn

  db_name  = "{node.params.get("database_name", "appdb")}"
  username = "occ_admin"
  manage_master_user_password = true

  iam_database_authentication_enabled = true

  db_subnet_group_name   = aws_db_subnet_group.{ident}.name
  vpc_security_group_ids = [aws_security_group.{ident}.id]
  parameter_group_name   = aws_db_parameter_group.{ident}.name
  publicly_accessible    = false

  multi_az                = {"true" if node.params.get("multi_az") else "false"}
  backup_retention_period = {int(node.params.get("backup_retention_days", 7))}
  copy_tags_to_snapshot   = true
  deletion_protection     = {"true" if plan.production else "false"}
  skip_final_snapshot     = {"false" if plan.production else "true"}
  final_snapshot_identifier = {final_snapshot}

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  performance_insights_enabled    = true
  auto_minor_version_upgrade      = true

{_tags_block(node)}
}}
{extra_vars}'''

    def _lambda(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        vpc_ident = tf_identifier(vpc.id) if vpc else None
        vpc_block = (
            f'''
  vpc_config {{
    subnet_ids         = aws_subnet.{vpc_ident}_private[*].id
    security_group_ids = [aws_security_group.{ident}.id]
  }}
'''
            if vpc_ident
            else ""
        )
        sg = (
            f'''resource "aws_security_group" "{ident}" {{
  name        = "{node.name}"
  description = "Egress for {node.id}"
  vpc_id      = aws_vpc.{vpc_ident}.id

  egress {{
    description = "HTTPS to AWS service endpoints inside the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.{vpc_ident}.cidr_block]
  }}

{_tags_block(node)}
}}

'''
            if vpc_ident
            else ""
        )
        reserved = int(node.params.get("reserved_concurrency", 0))
        reserved_line = (
            f"  reserved_concurrent_executions = {reserved}\n" if reserved > 0 else ""
        )
        return self._kms(node, "environment encryption") + f'''
{sg}resource "aws_cloudwatch_log_group" "{ident}" {{
  name              = "/aws/lambda/{node.name}"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.{ident}.arn
{_tags_block(node)}
}}

resource "aws_sqs_queue" "{ident}_dlq" {{
  name              = "{node.name}-dlq"
  kms_master_key_id = aws_kms_key.{ident}.arn
  tags              = merge(local.tags, {{ Component = "{node.id}", Role = "dlq" }})
}}

resource "aws_iam_role" "{ident}" {{
  name = "{node.name}"
  assume_role_policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect    = "Allow"
      Principal = {{ Service = "lambda.amazonaws.com" }}
      Action    = "sts:AssumeRole"
    }}]
  }})
{_tags_block(node)}
}}

resource "aws_iam_role_policy" "{ident}" {{
  name = "{node.name}"
  role = aws_iam_role.{ident}.id
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [
      {{
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${{aws_cloudwatch_log_group.{ident}.arn}}:*"
      }},
      {{
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.{ident}_dlq.arn
      }},
      {{
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = aws_kms_key.{ident}.arn
      }},
    ]
  }})
}}

resource "aws_iam_role_policy_attachment" "{ident}_vpc" {{
  role       = aws_iam_role.{ident}.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}}

data "archive_file" "{ident}" {{
  type        = "zip"
  source_dir  = "${{path.module}}/src/{node.id}"
  output_path = "${{path.module}}/build/{node.id}.zip"
}}

resource "aws_lambda_function" "{ident}" {{
  function_name = "{node.name}"
  role          = aws_iam_role.{ident}.arn
  runtime       = "{node.params.get("runtime", "python3.12")}"
  handler       = "{node.params.get("handler", "app.handler")}"
  memory_size   = {int(node.params.get("memory_mb", 512))}
  timeout       = {int(node.params.get("timeout_seconds", 30))}
{reserved_line}
  filename         = data.archive_file.{ident}.output_path
  source_code_hash = data.archive_file.{ident}.output_base64sha256
  kms_key_arn      = aws_kms_key.{ident}.arn
{vpc_block}
  dead_letter_config {{
    target_arn = aws_sqs_queue.{ident}_dlq.arn
  }}

  tracing_config {{
    mode = "Active"
  }}

  environment {{
    variables = {{
      ENVIRONMENT = var.environment
      SERVICE     = local.service
    }}
  }}

  depends_on = [aws_cloudwatch_log_group.{ident}]

{_tags_block(node)}
}}
'''

    def _ecs(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        vpc_ident = tf_identifier(vpc.id) if vpc else None
        subnets = (
            f"aws_subnet.{vpc_ident}_private[*].id" if vpc_ident else "var.vpc_private_subnet_ids"
        )
        vpc_id_ref = f"aws_vpc.{vpc_ident}.id" if vpc_ident else "var.vpc_id"
        cidr_ref = f"[aws_vpc.{vpc_ident}.cidr_block]" if vpc_ident else "var.vpc_cidr_blocks"
        image = node.params.get("image") or f"var.{ident}_image"
        image_expr = f'"{image}"' if node.params.get("image") else image
        port = int(node.params.get("container_port", 8080))
        return f'''resource "aws_ecs_cluster" "{ident}" {{
  name = "{node.name}-cluster"

  setting {{
    name  = "containerInsights"
    value = "enabled"
  }}

{_tags_block(node)}
}}

resource "aws_cloudwatch_log_group" "{ident}" {{
  name              = "/ecs/{node.name}"
  retention_in_days = 30
{_tags_block(node)}
}}

resource "aws_security_group" "{ident}" {{
  name        = "{node.name}"
  description = "Task networking for {node.id}"
  vpc_id      = {vpc_id_ref}

  ingress {{
    description = "Application traffic from the load balancer subnet range"
    from_port   = {port}
    to_port     = {port}
    protocol    = "tcp"
    cidr_blocks = {cidr_ref}
  }}

  egress {{
    description = "HTTPS egress to AWS service endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = {cidr_ref}
  }}

{_tags_block(node)}
}}

resource "aws_iam_role" "{ident}_execution" {{
  name = "{node.name}-exec"
  assume_role_policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect    = "Allow"
      Principal = {{ Service = "ecs-tasks.amazonaws.com" }}
      Action    = "sts:AssumeRole"
    }}]
  }})
{_tags_block(node)}
}}

resource "aws_iam_role_policy_attachment" "{ident}_execution" {{
  role       = aws_iam_role.{ident}_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}}

resource "aws_iam_role" "{ident}_task" {{
  name = "{node.name}-task"
  assume_role_policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect    = "Allow"
      Principal = {{ Service = "ecs-tasks.amazonaws.com" }}
      Action    = "sts:AssumeRole"
    }}]
  }})
{_tags_block(node)}
}}

resource "aws_iam_role_policy" "{ident}_task" {{
  name = "{node.name}-task"
  role = aws_iam_role.{ident}_task.id
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ]
      Resource = "${{aws_cloudwatch_log_group.{ident}.arn}}:*"
    }}]
  }})
}}

resource "aws_ecs_task_definition" "{ident}" {{
  family                   = "{node.name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "{int(node.params.get("cpu", 512))}"
  memory                   = "{int(node.params.get("memory_mb", 1024))}"
  execution_role_arn       = aws_iam_role.{ident}_execution.arn
  task_role_arn            = aws_iam_role.{ident}_task.arn

  container_definitions = jsonencode([{{
    name      = "{node.id}"
    image     = {image_expr}
    essential = true
    readonlyRootFilesystem = true

    portMappings = [{{
      containerPort = {port}
      protocol      = "tcp"
    }}]

    environment = [
      {{ name = "ENVIRONMENT", value = var.environment }},
      {{ name = "SERVICE", value = local.service }},
    ]

    healthCheck = {{
      command     = ["CMD-SHELL", "curl -fsS http://localhost:{port}{node.params.get("health_check_path", "/health")} || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }}

    logConfiguration = {{
      logDriver = "awslogs"
      options = {{
        "awslogs-group"         = aws_cloudwatch_log_group.{ident}.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "{node.id}"
      }}
    }}
  }}])

{_tags_block(node)}
}}

resource "aws_ecs_service" "{ident}" {{
  name            = "{node.name}"
  cluster         = aws_ecs_cluster.{ident}.id
  task_definition = aws_ecs_task_definition.{ident}.arn
  desired_count   = {int(node.params.get("desired_count", 2))}
  launch_type     = "FARGATE"

  enable_execute_command = false

  deployment_circuit_breaker {{
    enable   = true
    rollback = true
  }}

  network_configuration {{
    subnets          = {subnets}
    security_groups  = [aws_security_group.{ident}.id]
    assign_public_ip = false
  }}

{_tags_block(node)}
}}
'''

    def _observability(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        ident = tf_identifier(node.id)
        retention = int(node.params.get("log_retention_days", 30))
        alarms = []
        for target in plan.nodes:
            if target.type is NodeType.container_service:
                t = tf_identifier(target.id)
                alarms.append(f'''
resource "aws_cloudwatch_metric_alarm" "{ident}_{t}_cpu" {{
  alarm_name          = "{target.name}-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = 85
  alarm_description   = "Sustained CPU pressure on {target.id}"
  alarm_actions       = [aws_sns_topic.{ident}.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {{
    ClusterName = aws_ecs_cluster.{t}.name
    ServiceName = aws_ecs_service.{t}.name
  }}

{_tags_block(node)}
}}''')
            if target.type is NodeType.function:
                t = tf_identifier(target.id)
                alarms.append(f'''
resource "aws_cloudwatch_metric_alarm" "{ident}_{t}_errors" {{
  alarm_name          = "{target.name}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "{target.id} is returning errors"
  alarm_actions       = [aws_sns_topic.{ident}.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {{
    FunctionName = aws_lambda_function.{t}.function_name
  }}

{_tags_block(node)}
}}''')
        return self._kms(node, "log and topic encryption") + f'''
resource "aws_cloudwatch_log_group" "{ident}" {{
  name              = "/opencloudops/{plan.service}/{node.id}"
  retention_in_days = {retention}
  kms_key_id        = aws_kms_key.{ident}.arn
{_tags_block(node)}
}}

resource "aws_sns_topic" "{ident}" {{
  name              = "{node.name}"
  kms_master_key_id = aws_kms_key.{ident}.arn
{_tags_block(node)}
}}

resource "aws_sns_topic_subscription" "{ident}" {{
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.{ident}.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}}
{"".join(alarms)}
'''
