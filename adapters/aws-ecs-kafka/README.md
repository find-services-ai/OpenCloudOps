# Adapter: AWS ECS + Kafka (Streaming Platform)
#
# Use this adapter when your platform runs:
# - ECS Fargate services (consumers, producers, APIs)
# - Amazon MSK or self-managed Kafka
# - RDS PostgreSQL or compatible relational database
# - CodeBuild CI/CD
#
# This adapter provides production-tested patterns for
# managing 10+ ECS services, MSK cluster, RDS PostgreSQL,
# and full self-healing deployment automation.

## platform.yml overrides

```yaml
compute:
  type: ecs_fargate
  health_endpoint_pattern: "/health"
  registry:
    type: ecr

streaming:
  enabled: true
  type: msk
  auth: iam
  port: 9098

database:
  type: rds_postgresql
  iam_auth: true

cicd:
  type: codebuild
```

## Domain Skills Included

### kafka-topic-management
- Create/update topics via Kafka Admin Lambda
- Topic naming: `{app}-{env}-{domain}-{entity}`
- Partition strategy based on throughput

### consumer-lag-monitor
- NRQL: `SELECT max(kafka.consumer.totalLag) FROM Metric`
- Thresholds: < 1000 healthy, 1000-10000 degraded, > 10000 unhealthy
- Auto-correlate with consumer error logs

### msk-iam-policy-check
- Validate producer policies include `WriteDataIdempotently`
- Validate consumer policies include `AlterGroup` + `DescribeGroup`
- Check SG rules for port 9098 bidirectional

### ecs-task-triage
- OOM kill detection via exit code 137
- Crash-loop detection via restart count
- Health check failure correlation with startup logs

## Terraform Patterns

### ECS Service `for_each`

```hcl
variable "ecs_services" {
  type = map(object({
    repository        = string
    tag               = string
    cpu               = optional(number, 512)
    memory            = optional(number, 1024)
    desired_count     = optional(number, 1)
    enable_alb        = optional(bool, false)
    postgres_iam_auth = optional(bool, false)
    # ...
  }))
}

module "ecs_service" {
  for_each = var.ecs_services
  source   = "your-module-source"

  service_name    = each.key
  container_image = "${each.value.repository}:${each.value.tag}"
  cpu             = each.value.cpu
  memory          = each.value.memory
  desired_count   = each.value.desired_count
  # ...
}
```

### MSK IAM Policy Template

```hcl
# Producer
data "aws_iam_policy_document" "kafka_producer" {
  statement {
    actions = [
      "kafka-cluster:Connect",
      "kafka-cluster:DescribeCluster",
      "kafka-cluster:WriteDataIdempotently",  # ← Easy to forget!
    ]
    resources = [var.msk_cluster_arn]
  }
  statement {
    actions = [
      "kafka-cluster:WriteData",
      "kafka-cluster:DescribeTopic",
    ]
    resources = ["${var.msk_cluster_arn}/topic/${var.topic_prefix}*"]
  }
}

# Consumer
data "aws_iam_policy_document" "kafka_consumer" {
  statement {
    actions = [
      "kafka-cluster:Connect",
      "kafka-cluster:DescribeCluster",
    ]
    resources = [var.msk_cluster_arn]
  }
  statement {
    actions = [
      "kafka-cluster:ReadData",
      "kafka-cluster:DescribeTopic",
    ]
    resources = ["${var.msk_cluster_arn}/topic/${var.topic_prefix}*"]
  }
  statement {
    actions = [
      "kafka-cluster:AlterGroup",
      "kafka-cluster:DescribeGroup",
    ]
    resources = ["${var.msk_cluster_arn}/group/${var.consumer_group_prefix}*"]
  }
}
```

## Env-Leak Patterns

```yaml
leak_patterns:
  dev_must_not_contain:
    - "{staging_account_id}"
    - "{prod_account_id}"
    - "staging-broker-endpoint"
    - "prod-broker-endpoint"
  prod_must_not_contain:
    - "{dev_account_id}"
    - "{staging_account_id}"
    - "dev-broker-endpoint"
    - "staging-broker-endpoint"
```
