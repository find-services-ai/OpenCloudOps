# Adapter: AWS Serverless (Lambda + API Gateway + DynamoDB)
#
# Use this adapter when your platform runs:
# - Lambda functions (event-driven or API-backed)
# - API Gateway (REST or HTTP)
# - DynamoDB tables
# - Step Functions for orchestration
# - EventBridge for event routing

## platform.yml overrides

```yaml
compute:
  type: lambda
  health_endpoint_pattern: null              # Lambdas don't have health endpoints
  registry:
    type: ecr                                 # or s3 for ZIP deployments
    uri_pattern: "{account}.dkr.ecr.{region}.amazonaws.com/{repo}:{tag}"
  image_source: tfvars

streaming:
  enabled: true
  type: eventbridge
  auth: iam

database:
  type: dynamodb
  iam_auth: true

cicd:
  type: github_actions                        # or codebuild
  plan_project: "terraform-plan"
  apply_project: "terraform-apply"

observability:
  apm:
    type: datadog                             # or xray
  logs:
    type: cloudwatch
    group_pattern: "/aws/lambda/{service}"
```

## Domain Skills Included

### lambda-cold-start-optimizer
- Detect cold start patterns from CloudWatch logs
- Recommend SnapStart, provisioned concurrency, or code optimization
- Monitor p99 latency for cold start impact

### api-gw-cors-triage
- Parse 4xx errors from API Gateway logs
- Detect missing CORS headers
- Validate OPTIONS method configuration

### step-fn-execution-triage
- Analyze failed Step Function executions
- Map failure to specific state + error
- Suggest retry/catch configuration

### dynamodb-throttle-monitor
- Monitor `ReadThrottleEvents` and `WriteThrottleEvents`
- Recommend capacity mode (on-demand vs provisioned)
- Detect hot partition patterns

## Terraform Patterns

### Lambda `for_each`

```hcl
variable "functions" {
  type = map(object({
    handler       = string
    runtime       = string
    memory_size   = optional(number, 256)
    timeout       = optional(number, 30)
    image_uri     = optional(string)
    environment   = optional(map(string), {})
    event_sources = optional(list(string), [])
  }))
}

module "lambda_function" {
  for_each = var.functions
  source   = "terraform-aws-modules/lambda/aws"

  function_name = "${local.prefix}-${each.key}"
  handler       = each.value.handler
  runtime       = each.value.runtime
  memory_size   = each.value.memory_size
  timeout       = each.value.timeout

  environment_variables = merge(
    local.common_env_vars,
    each.value.environment,
  )
}
```

### Service Registry Entry (Lambda)

```markdown
| Service Key | Health Signal | Degraded | Unhealthy |
|---|---|---|---|
| `my-api-fn` | Invocation success rate > 99% | 95-99% success | < 95% or timeout spike |
| `my-processor` | DLQ empty, processing < 5min | DLQ growing slowly | DLQ spike or timeout |
```
