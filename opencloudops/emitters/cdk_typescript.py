"""AWS CDK (TypeScript) backend.

CDK is the one backend where the L2 constructs already encode a good part of
the security baseline (``encryption: BucketEncryption.KMS`` brings its own key
policy; ``blockPublicAccess`` is a first-class argument). That makes the
generated stack short -- and it makes it important that the generator does not
quietly rely on a CDK default that a future major version might flip. Every
control the guard checks for is stated explicitly here even when the current
default happens to match.
"""

from __future__ import annotations

import json

from opencloudops.emitters.base import Emitter
from opencloudops.naming import logical_id, tf_identifier
from opencloudops.spec import NodeType, PlatformContract, ResolvedNode, ResolvedPlan


def _ts(value: object) -> str:
    return json.dumps(value)


class CdkTypeScriptEmitter(Emitter):
    id = "cdk-typescript"
    language = "AWS CDK (TypeScript)"
    description = "A CDK v2 app: stack, entrypoint, package.json, tsconfig and cdk.json."

    def emit(self, plan: ResolvedPlan, platform: PlatformContract) -> dict[str, str]:
        stack_class = logical_id(plan.graph_name, "Stack")
        files = {
            f"lib/{plan.graph_name}-stack.ts": self._stack(plan, platform, stack_class),
            f"bin/{plan.graph_name}.ts": self._entrypoint(plan, stack_class),
            "package.json": self._package(plan),
            "tsconfig.json": self._tsconfig(),
            "cdk.json": self._cdk_json(plan),
            "README.md": self._readme(plan),
        }
        files.update(self.skip_note(plan))
        return files

    def _package(self, plan: ResolvedPlan) -> str:
        return json.dumps(
            {
                "name": plan.graph_name,
                "version": "0.1.0",
                "private": True,
                "bin": {plan.graph_name: f"bin/{plan.graph_name}.js"},
                "scripts": {
                    "build": "tsc",
                    "synth": "cdk synth",
                    "diff": "cdk diff",
                },
                "devDependencies": {
                    "aws-cdk": "^2.150.0",
                    "typescript": "~5.5.0",
                    "@types/node": "^20.14.0",
                },
                "dependencies": {"aws-cdk-lib": "^2.150.0", "constructs": "^10.3.0"},
            },
            indent=2,
        ) + "\n"

    def _tsconfig(self) -> str:
        return json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "commonjs",
                    "lib": ["ES2022"],
                    "strict": True,
                    "noImplicitAny": True,
                    "strictNullChecks": True,
                    "esModuleInterop": True,
                    "skipLibCheck": True,
                    "declaration": True,
                    "outDir": ".",
                },
                "exclude": ["cdk.out", "node_modules"],
            },
            indent=2,
        ) + "\n"

    def _cdk_json(self, plan: ResolvedPlan) -> str:
        return json.dumps(
            {
                "app": f"npx ts-node --prefer-ts-exts bin/{plan.graph_name}.ts",
                "context": {
                    "environment": plan.environment,
                    "@aws-cdk/core:enableStackNameDuplicates": False,
                    "@aws-cdk/aws-iam:minimizePolicies": True,
                },
            },
            indent=2,
        ) + "\n"

    def _readme(self, plan: ResolvedPlan) -> str:
        return f"""# {plan.graph_name} -- AWS CDK ({plan.environment})

```bash
npm install
npm run synth
npx cdk diff
```

`cdk deploy` is intentionally not wired into a script. Deployment is gated at
HIGH in `agent-graph.yaml`; a human runs it after reading the diff.
"""

    def _entrypoint(self, plan: ResolvedPlan, stack_class: str) -> str:
        return self.header(plan, "//") + f"""import {{ App, Tags }} from 'aws-cdk-lib';
import {{ {stack_class} }} from '../lib/{plan.graph_name}-stack';

const app = new App();

const stack = new {stack_class}(app, '{logical_id(plan.service, plan.environment)}', {{
  env: {{ region: {_ts(plan.region)} }},
  environmentName: {_ts(plan.environment)},
}});

// Tags applied at the app level reach every construct, including the ones CDK
// creates implicitly (log groups, roles) that a per-resource loop would miss.
{self._app_tags(plan)}
"""

    def _app_tags(self, plan: ResolvedPlan) -> str:
        if not plan.nodes:
            return ""
        tags = dict(sorted(plan.nodes[0].tags.items()))
        tags.pop("Component", None)
        return "\n".join(f"Tags.of(stack).add({_ts(k)}, {_ts(v)});" for k, v in tags.items())

    # -- stack ----------------------------------------------------------

    def _stack(self, plan: ResolvedPlan, platform: PlatformContract, stack_class: str) -> str:
        body: list[str] = []
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
            body.append(f"    // {node.id}: {node.type.value} via pattern {node.pattern_id}")
            body.append(render(node, plan))

        return self.header(plan, "//") + f"""import {{
  Stack,
  StackProps,
  Duration,
  RemovalPolicy,
  CfnOutput,
  aws_ec2 as ec2,
  aws_ecs as ecs,
  aws_s3 as s3,
  aws_kms as kms,
  aws_iam as iam,
  aws_sqs as sqs,
  aws_rds as rds,
  aws_lambda as lambda_,
  aws_logs as logs,
  aws_sns as sns,
  aws_sns_subscriptions as subs,
  aws_dynamodb as dynamodb,
  aws_cloudwatch as cloudwatch,
  aws_cloudwatch_actions as cwActions,
  aws_secretsmanager as secretsmanager,
}} from 'aws-cdk-lib';
import {{ Construct }} from 'constructs';

export interface {stack_class}Props extends StackProps {{
  readonly environmentName: string;
}}

export class {stack_class} extends Stack {{
  constructor(scope: Construct, id: string, props: {stack_class}Props) {{
    super(scope, id, props);

    const environmentName = props.environmentName;
    const service = {_ts(plan.service)};
    const isProduction = {str(plan.production).lower()};
    const removalPolicy = isProduction ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY;

{chr(10).join(body)}
  }}
}}
"""

    def _key(self, node: ResolvedNode, purpose: str) -> str:
        v = tf_identifier(node.id)
        return f"""    const {v}Key = new kms.Key(this, '{logical_id(node.id, "Key")}', {{
      description: 'CMK for {node.id} ({purpose})',
      enableKeyRotation: true,
      alias: '{node.name}',
      removalPolicy,
    }});
"""

    def _vpc(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        azs = int(node.params.get("availability_zones", 2))
        nat = 1 if node.params.get("single_nat_gateway") else azs
        return f"""    const {v} = new ec2.Vpc(this, '{logical_id(node.id)}', {{
      ipAddresses: ec2.IpAddresses.cidr({_ts(node.params.get("cidr", "10.0.0.0/16"))}),
      maxAzs: {azs},
      natGateways: {nat},
      subnetConfiguration: [
        {{ name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 }},
        {{ name: 'private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 }},
        {{ name: 'isolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 }},
      ],
      flowLogs: {{
        all: {{
          destination: ec2.FlowLogDestination.toCloudWatchLogs(
            new logs.LogGroup(this, '{logical_id(node.id, "FlowLogs")}', {{
              logGroupName: '/aws/vpc/{node.name}/flow-logs',
              retention: logs.RetentionDays.THREE_MONTHS,
              removalPolicy,
            }}),
          ),
          trafficType: ec2.FlowLogTrafficType.ALL,
        }},
      }},
    }});

    new CfnOutput(this, '{logical_id(node.id, "Id")}', {{ value: {v}.vpcId }});
"""

    def _s3(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        expire = int(node.params.get("lifecycle_expire_days", 0))
        lifecycle = (
            f"""
      lifecycleRules: [{{
        id: 'expire',
        enabled: true,
        expiration: Duration.days({expire}),
        noncurrentVersionExpiration: Duration.days(30),
      }}],"""
            if expire > 0
            else ""
        )
        return self._key(node, "object encryption") + f"""    const {v} = new s3.Bucket(this, '{logical_id(node.id)}', {{
      bucketName: {_ts(node.name)},
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: {v}Key,
      bucketKeyEnabled: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: {str(bool(node.params.get("versioning", True))).lower()},{lifecycle}
      removalPolicy,
    }});

    new CfnOutput(this, '{logical_id(node.id, "Name")}', {{ value: {v}.bucketName }});
"""

    def _dynamodb(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        sk = node.params.get("sort_key") or ""
        sort = (
            f"\n      sortKey: {{ name: {_ts(sk)}, type: dynamodb.AttributeType.STRING }},"
            if sk
            else ""
        )
        ttl = (
            f"\n      timeToLiveAttribute: {_ts(node.params['ttl_attribute'])},"
            if node.params.get("ttl_attribute")
            else ""
        )
        stream = (
            "\n      stream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,"
            if node.params.get("stream")
            else ""
        )
        billing = (
            "dynamodb.BillingMode.PAY_PER_REQUEST"
            if node.params.get("billing_mode", "PAY_PER_REQUEST") == "PAY_PER_REQUEST"
            else "dynamodb.BillingMode.PROVISIONED"
        )
        return self._key(node, "table encryption") + f"""    const {v} = new dynamodb.Table(this, '{logical_id(node.id)}', {{
      tableName: {_ts(node.name)},
      partitionKey: {{ name: {_ts(node.params["partition_key"])}, type: dynamodb.AttributeType.STRING }},{sort}
      billingMode: {billing},
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: {v}Key,
      pointInTimeRecoverySpecification: {{ pointInTimeRecoveryEnabled: true }},
      deletionProtection: isProduction,{ttl}{stream}
      removalPolicy,
    }});

    new CfnOutput(this, '{logical_id(node.id, "Name")}', {{ value: {v}.tableName }});
"""

    def _sqs(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        fifo = bool(node.params.get("fifo", False))
        suffix = ".fifo" if fifo else ""
        fifo_line = "\n      fifo: true," if fifo else ""
        return self._key(node, "queue encryption") + f"""    const {v}Dlq = new sqs.Queue(this, '{logical_id(node.id, "Dlq")}', {{
      queueName: {_ts(node.name + "-dlq" + suffix)},{fifo_line}
      encryption: sqs.QueueEncryption.KMS,
      encryptionMasterKey: {v}Key,
      enforceSSL: true,
      retentionPeriod: Duration.days(14),
    }});

    const {v} = new sqs.Queue(this, '{logical_id(node.id)}', {{
      queueName: {_ts(node.name + suffix)},{fifo_line}
      encryption: sqs.QueueEncryption.KMS,
      encryptionMasterKey: {v}Key,
      enforceSSL: true,
      visibilityTimeout: Duration.seconds({int(node.params.get("visibility_timeout_seconds", 30))}),
      retentionPeriod: Duration.seconds({int(node.params.get("message_retention_seconds", 345600))}),
      deadLetterQueue: {{ queue: {v}Dlq, maxReceiveCount: {int(node.params.get("max_receive_count", 5))} }},
    }});

    new CfnOutput(this, '{logical_id(node.id, "Url")}', {{ value: {v}.queueUrl }});
"""

    def _secret(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        return self._key(node, "secret encryption") + f"""    const {v} = new secretsmanager.Secret(this, '{logical_id(node.id)}', {{
      secretName: {_ts(node.name)},
      description: {_ts(node.params.get("description", "Managed by OpenCloudOps"))},
      encryptionKey: {v}Key,
      generateSecretString: {{
        secretStringTemplate: JSON.stringify({{ managed_by: 'opencloudops' }}),
        generateStringKey: 'value',
        passwordLength: 32,
        excludePunctuation: true,
      }},
      // A secret's value outlives any one stack: rotation writes it, IaC must not.
      removalPolicy: RemovalPolicy.RETAIN,
    }});

    new CfnOutput(this, '{logical_id(node.id, "Arn")}', {{ value: {v}.secretArn }});
"""

    def _rds(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        if vpc is None:
            return (
                f"    // {node.id} requires a net.vpc node in the same graph. "
                "Add one and re-run `occ iac`.\n"
            )
        vv = tf_identifier(vpc.id)
        return self._key(node, "storage encryption") + f"""    const {v}Sg = new ec2.SecurityGroup(this, '{logical_id(node.id, "Sg")}', {{
      vpc: {vv},
      description: 'Database ingress for {plan.service}',
      allowAllOutbound: false,
    }});
    {v}Sg.addIngressRule(
      ec2.Peer.ipv4({vv}.vpcCidrBlock),
      ec2.Port.tcp(5432),
      'PostgreSQL from inside the VPC only',
    );

    const {v} = new rds.DatabaseInstance(this, '{logical_id(node.id)}', {{
      instanceIdentifier: {_ts(node.name)},
      engine: rds.DatabaseInstanceEngine.postgres({{
        version: rds.PostgresEngineVersion.of({_ts(node.params.get("engine_version", "16.4"))}, '16'),
      }}),
      instanceType: new ec2.InstanceType({_ts(node.params.get("instance_class", "db.t4g.medium").replace("db.", ""))}),
      vpc: {vv},
      vpcSubnets: {{ subnetType: ec2.SubnetType.PRIVATE_ISOLATED }},
      securityGroups: [{v}Sg],
      databaseName: {_ts(node.params.get("database_name", "appdb"))},
      credentials: rds.Credentials.fromGeneratedSecret('occ_admin', {{ encryptionKey: {v}Key }}),
      iamAuthentication: true,
      allocatedStorage: {int(node.params.get("allocated_storage_gb", 50))},
      maxAllocatedStorage: {int(node.params.get("allocated_storage_gb", 50)) * 4},
      storageType: rds.StorageType.GP3,
      storageEncrypted: true,
      storageEncryptionKey: {v}Key,
      multiAz: {str(bool(node.params.get("multi_az", False))).lower()},
      backupRetention: Duration.days({int(node.params.get("backup_retention_days", 7))}),
      copyTagsToSnapshot: true,
      deletionProtection: isProduction,
      publiclyAccessible: false,
      cloudwatchLogsExports: ['postgresql', 'upgrade'],
      enablePerformanceInsights: true,
      autoMinorVersionUpgrade: true,
      removalPolicy: isProduction ? RemovalPolicy.SNAPSHOT : RemovalPolicy.DESTROY,
    }});

    new CfnOutput(this, '{logical_id(node.id, "Endpoint")}', {{
      value: {v}.dbInstanceEndpointAddress,
    }});
"""

    def _lambda(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        vpc_lines = (
            f"""
      vpc: {tf_identifier(vpc.id)},
      vpcSubnets: {{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }},"""
            if vpc
            else ""
        )
        reserved = int(node.params.get("reserved_concurrency", 0))
        reserved_line = (
            f"\n      reservedConcurrentExecutions: {reserved}," if reserved > 0 else ""
        )
        runtime = node.params.get("runtime", "python3.12")
        runtime_enum = {
            "python3.12": "PYTHON_3_12",
            "python3.11": "PYTHON_3_11",
            "nodejs20.x": "NODEJS_20_X",
            "java21": "JAVA_21",
            "provided.al2023": "PROVIDED_AL2023",
        }.get(runtime, "PYTHON_3_12")
        return self._key(node, "environment encryption") + f"""    const {v}Dlq = new sqs.Queue(this, '{logical_id(node.id, "Dlq")}', {{
      queueName: {_ts(node.name + "-dlq")},
      encryption: sqs.QueueEncryption.KMS,
      encryptionMasterKey: {v}Key,
      enforceSSL: true,
    }});

    const {v} = new lambda_.Function(this, '{logical_id(node.id)}', {{
      functionName: {_ts(node.name)},
      runtime: lambda_.Runtime.{runtime_enum},
      handler: {_ts(node.params.get("handler", "app.handler"))},
      code: lambda_.Code.fromAsset('src/{node.id}'),
      memorySize: {int(node.params.get("memory_mb", 512))},
      timeout: Duration.seconds({int(node.params.get("timeout_seconds", 30))}),{reserved_line}
      environmentEncryption: {v}Key,
      deadLetterQueue: {v}Dlq,
      tracing: lambda_.Tracing.ACTIVE,
      logRetention: logs.RetentionDays.ONE_MONTH,{vpc_lines}
      environment: {{ ENVIRONMENT: environmentName, SERVICE: service }},
    }});

    new CfnOutput(this, '{logical_id(node.id, "Arn")}', {{ value: {v}.functionArn }});
"""

    def _ecs(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        if vpc is None:
            return (
                f"    // {node.id} requires a net.vpc node in the same graph. "
                "Add one and re-run `occ iac`.\n"
            )
        vv = tf_identifier(vpc.id)
        port = int(node.params.get("container_port", 8080))
        health = node.params.get("health_check_path", "/health")
        image = node.params.get("image")
        image_expr = (
            f"ecs.ContainerImage.fromRegistry({_ts(image)})"
            if image
            else f"ecs.ContainerImage.fromRegistry(this.node.tryGetContext('{v}Image') ?? 'REPLACE/{node.id}:TAG')"
        )
        return f"""    const {v}Cluster = new ecs.Cluster(this, '{logical_id(node.id, "Cluster")}', {{
      clusterName: {_ts(node.name + "-cluster")},
      vpc: {vv},
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    }});

    const {v}LogGroup = new logs.LogGroup(this, '{logical_id(node.id, "Logs")}', {{
      logGroupName: '/ecs/{node.name}',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy,
    }});

    const {v}Task = new ecs.FargateTaskDefinition(this, '{logical_id(node.id, "Task")}', {{
      family: {_ts(node.name)},
      cpu: {int(node.params.get("cpu", 512))},
      memoryLimitMiB: {int(node.params.get("memory_mb", 1024))},
    }});

    {v}Task.addContainer({_ts(node.id)}, {{
      image: {image_expr},
      essential: true,
      readonlyRootFilesystem: true,
      portMappings: [{{ containerPort: {port}, protocol: ecs.Protocol.TCP }}],
      environment: {{ ENVIRONMENT: environmentName, SERVICE: service }},
      healthCheck: {{
        command: ['CMD-SHELL', 'curl -fsS http://localhost:{port}{health} || exit 1'],
        interval: Duration.seconds(30),
        timeout: Duration.seconds(5),
        retries: 3,
        startPeriod: Duration.seconds(20),
      }},
      logging: ecs.LogDrivers.awsLogs({{ streamPrefix: {_ts(node.id)}, logGroup: {v}LogGroup }}),
    }});

    const {v}Sg = new ec2.SecurityGroup(this, '{logical_id(node.id, "Sg")}', {{
      vpc: {vv},
      description: 'Task networking for {node.id}',
      allowAllOutbound: false,
    }});
    {v}Sg.addIngressRule(
      ec2.Peer.ipv4({vv}.vpcCidrBlock),
      ec2.Port.tcp({port}),
      'Application traffic from inside the VPC',
    );
    {v}Sg.addEgressRule(
      ec2.Peer.ipv4({vv}.vpcCidrBlock),
      ec2.Port.tcp(443),
      'HTTPS egress to AWS service endpoints',
    );

    const {v} = new ecs.FargateService(this, '{logical_id(node.id)}', {{
      serviceName: {_ts(node.name)},
      cluster: {v}Cluster,
      taskDefinition: {v}Task,
      desiredCount: {int(node.params.get("desired_count", 2))},
      assignPublicIp: false,
      enableExecuteCommand: false,
      vpcSubnets: {{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }},
      securityGroups: [{v}Sg],
      circuitBreaker: {{ rollback: true }},
    }});

    new CfnOutput(this, '{logical_id(node.id, "Service")}', {{ value: {v}.serviceName }});
"""

    def _observability(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        alarms = []
        for target in plan.nodes:
            t = tf_identifier(target.id)
            if target.type is NodeType.container_service:
                alarms.append(f"""
    new cloudwatch.Alarm(this, '{logical_id(node.id, target.id, "Cpu")}', {{
      alarmName: {_ts(target.name + "-cpu-high")},
      alarmDescription: 'Sustained CPU pressure on {target.id}',
      metric: {t}.metricCpuUtilization({{ period: Duration.minutes(5) }}),
      threshold: 85,
      evaluationPeriods: 3,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }}).addAlarmAction(new cwActions.SnsAction({v}Topic));
""")
            if target.type is NodeType.function:
                alarms.append(f"""
    new cloudwatch.Alarm(this, '{logical_id(node.id, target.id, "Errors")}', {{
      alarmName: {_ts(target.name + "-errors")},
      alarmDescription: '{target.id} is returning errors',
      metric: {t}.metricErrors({{ period: Duration.minutes(5) }}),
      threshold: 1,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }}).addAlarmAction(new cwActions.SnsAction({v}Topic));
""")
        retention = {
            1: "ONE_DAY", 7: "ONE_WEEK", 14: "TWO_WEEKS", 30: "ONE_MONTH",
            60: "TWO_MONTHS", 90: "THREE_MONTHS", 180: "SIX_MONTHS", 365: "ONE_YEAR",
        }.get(int(node.params.get("log_retention_days", 30)), "ONE_MONTH")
        alarm_email = node.params.get("alarm_email", "")
        subscription = (
            f"\n    {v}Topic.addSubscription(new subs.EmailSubscription({_ts(alarm_email)}));"
            if alarm_email
            else f"\n    // No alarm_email set on {node.id}: add a subscription before this reaches production."
        )
        return self._key(node, "log and topic encryption") + f"""    const {v}Topic = new sns.Topic(this, '{logical_id(node.id, "Topic")}', {{
      topicName: {_ts(node.name)},
      masterKey: {v}Key,
    }});{subscription}

    new logs.LogGroup(this, '{logical_id(node.id, "Logs")}', {{
      logGroupName: '/opencloudops/{plan.service}/{node.id}',
      retention: logs.RetentionDays.{retention},
      encryptionKey: {v}Key,
      removalPolicy,
    }});
{"".join(alarms)}"""
