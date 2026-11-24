# Adapter: AWS EKS + Kinesis (K8s Streaming Platform)
#
# Use this adapter when your platform runs:
# - EKS (Kubernetes) with Helm-managed deployments
# - Amazon Kinesis Data Streams
# - RDS PostgreSQL or compatible databases
# - GitHub Actions or GitLab CI

## platform.yml overrides

```yaml
compute:
  type: eks
  cluster_naming: "{app}-{env}-cluster"
  service_naming: "{app}-{env}-{service}"
  health_endpoint_pattern: "/healthz"
  registry:
    type: ecr
  image_source: helm_values                   # Helm values.yaml per env

streaming:
  enabled: true
  type: kinesis
  auth: iam
  # No port needed — Kinesis is API-based

database:
  type: rds_postgresql
  iam_auth: true

cicd:
  type: github_actions
  plan_project: "terraform-plan"
  apply_project: "terraform-apply"
  log_group: null                             # GitHub Actions logs via API

observability:
  apm:
    type: datadog
    app_naming: "{app}-{env}-{service}"
    query_language: dql
  logs:
    type: cloudwatch
    group_pattern: "/aws/eks/{cluster}/containers/{namespace}/{service}"
```

## Domain Skills Included

### helm-release-manager
- Compare Helm values across environments
- Promote image tags via values.yaml diffs
- Validate chart compatibility before upgrade

### k8s-pod-triage
- CrashLoopBackOff diagnosis via pod events
- OOMKilled detection from container status
- ImagePullBackOff → registry/tag validation
- Pending pod → resource quota / node capacity check

### kinesis-shard-monitor
- Monitor `GetRecords.IteratorAgeMilliseconds`
- Detect hot shards via `IncomingBytes` per shard
- Recommend shard split/merge based on throughput

### istio-mesh-health (optional)
- Circuit breaker trip detection
- Retry budget exhaustion
- mTLS certificate expiry monitoring

## Terraform Patterns

### EKS + Helm for_each

```hcl
variable "services" {
  type = map(object({
    chart_version = string
    image_tag     = string
    replicas      = optional(number, 2)
    values_file   = optional(string)
    namespace     = optional(string, "default")
  }))
}

resource "helm_release" "service" {
  for_each = var.services

  name       = each.key
  namespace  = each.value.namespace
  chart      = "./charts/${each.key}"
  version    = each.value.chart_version

  set {
    name  = "image.tag"
    value = each.value.image_tag
  }

  set {
    name  = "replicaCount"
    value = each.value.replicas
  }

  values = each.value.values_file != null ? [
    file("${path.module}/values/${each.value.values_file}")
  ] : []
}
```

### Kinesis IAM Policy Template

```hcl
data "aws_iam_policy_document" "kinesis_consumer" {
  statement {
    actions = [
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:ListShards",
      "kinesis:SubscribeToShard",
    ]
    resources = [aws_kinesis_stream.main.arn]
  }
  statement {
    actions   = ["dynamodb:*"]
    resources = [aws_dynamodb_table.kinesis_checkpoint.arn]
    # KCL checkpoint table
  }
}
```
