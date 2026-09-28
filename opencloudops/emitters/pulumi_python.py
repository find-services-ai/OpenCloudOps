"""Pulumi (Python) backend.

Pulumi's value over a templating language is that infrastructure is ordinary
code, so this backend leans on that: shared tags are a module-level dict,
dependency order is expressed through Python references rather than a
`depends_on` list, and the environment comes from Pulumi config rather than
being baked into the file.
"""

from __future__ import annotations

import json

from opencloudops.emitters.base import Emitter
from opencloudops.naming import tf_identifier
from opencloudops.spec import NodeType, PlatformContract, ResolvedNode, ResolvedPlan


def _py(value: object) -> str:
    return json.dumps(value)


class PulumiPythonEmitter(Emitter):
    id = "pulumi-python"
    language = "Pulumi (Python)"
    description = "A Pulumi Python program plus its project and stack configuration."

    def emit(self, plan: ResolvedPlan, platform: PlatformContract) -> dict[str, str]:
        files = {
            "__main__.py": self._program(plan, platform),
            "Pulumi.yaml": self._project(plan),
            f"Pulumi.{plan.environment}.yaml": self._stack(plan),
            "requirements.txt": "pulumi>=3.120.0\npulumi-aws>=6.40.0\n",
            "README.md": self._readme(plan),
        }
        files.update(self.skip_note(plan))
        return files

    def _project(self, plan: ResolvedPlan) -> str:
        return (
            f"name: {plan.graph_name}\n"
            "runtime:\n"
            "  name: python\n"
            f"description: OpenCloudOps plan for {plan.service}\n"
        )

    def _stack(self, plan: ResolvedPlan) -> str:
        lines = [
            "config:",
            f"  aws:region: {plan.region}",
            f"  {plan.graph_name}:environment: {plan.environment}",
            f'  {plan.graph_name}:alarmEmail: ""',
        ]
        for node in plan.by_type(NodeType.container_service):
            if not node.params.get("image"):
                lines.append(f"  {plan.graph_name}:{tf_identifier(node.id)}Image: REPLACE-ME")
        return "\n".join(lines) + "\n"

    def _readme(self, plan: ResolvedPlan) -> str:
        return f"""# {plan.graph_name} -- Pulumi ({plan.environment})

```bash
pip install -r requirements.txt
pulumi stack select {plan.environment}
pulumi preview
```

`pulumi up` is not run for you. Applying is gated at HIGH in `agent-graph.yaml`.
"""

    def _program(self, plan: ResolvedPlan, platform: PlatformContract) -> str:
        tags = dict(sorted(plan.nodes[0].tags.items())) if plan.nodes else {}
        tags.pop("Component", None)
        parts = [
            self.header(plan),
            "import json",
            "",
            "import pulumi",
            "import pulumi_aws as aws",
            "",
            "config = pulumi.Config()",
            f'ENVIRONMENT = config.get("environment") or {_py(plan.environment)}',
            f'SERVICE = {_py(plan.service)}',
            'ALARM_EMAIL = config.get("alarmEmail") or ""',
            "",
            "# One shared tag map. A tagging change is one edit here, not one per resource.",
            f"TAGS = {json.dumps(tags, indent=4)}",
            "",
            "",
            "def tags(component: str) -> dict:",
            '    return {**TAGS, "Component": component}',
            "",
            "",
            "",
            "def _cidr(vpc_cidr: str, index: int) -> str:",
            '    """Carve a /24 out of the VPC block. Pulumi has no cidrsubnet(),',
            "    so the arithmetic is explicit rather than hidden in a provider call.",
            '    """',
            "    import ipaddress",
            "",
            "    return str(list(ipaddress.ip_network(vpc_cidr).subnets(new_prefix=24))[index])",
            "",
            "",
            "account = aws.get_caller_identity()",
            "",
        ]
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
            parts.append("")
        return "\n".join(parts)

    def _kms(self, node: ResolvedNode, purpose: str) -> str:
        v = tf_identifier(node.id)
        return f'''{v}_key = aws.kms.Key(
    "{node.id}-key",
    description="CMK for {node.id} ({purpose})",
    enable_key_rotation=True,
    deletion_window_in_days=30,
    tags=tags({_py(node.id)}),
)

{v}_alias = aws.kms.Alias(
    "{node.id}-key-alias",
    name="alias/{node.name}",
    target_key_id={v}_key.key_id,
)
'''

    def _vpc(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        azs = int(node.params.get("availability_zones", 2))
        nat_count = 1 if node.params.get("single_nat_gateway") else azs
        return f'''{v} = aws.ec2.Vpc(
    "{node.id}",
    cidr_block={_py(node.params.get("cidr", "10.0.0.0/16"))},
    enable_dns_support=True,
    enable_dns_hostnames=True,
    tags=tags({_py(node.id)}),
)

{v}_azs = aws.get_availability_zones(state="available")

{v}_public_subnets = [
    aws.ec2.Subnet(
        f"{node.id}-public-{{i}}",
        vpc_id={v}.id,
        availability_zone={v}_azs.names[i],
        cidr_block={v}.cidr_block.apply(lambda c, i=i: _cidr(c, i)),
        map_public_ip_on_launch=False,
        tags={{**tags({_py(node.id)}), "Tier": "public"}},
    )
    for i in range({azs})
]

{v}_private_subnets = [
    aws.ec2.Subnet(
        f"{node.id}-private-{{i}}",
        vpc_id={v}.id,
        availability_zone={v}_azs.names[i],
        cidr_block={v}.cidr_block.apply(lambda c, i=i: _cidr(c, i + 10)),
        tags={{**tags({_py(node.id)}), "Tier": "private"}},
    )
    for i in range({azs})
]

{v}_isolated_subnets = [
    aws.ec2.Subnet(
        f"{node.id}-isolated-{{i}}",
        vpc_id={v}.id,
        availability_zone={v}_azs.names[i],
        cidr_block={v}.cidr_block.apply(lambda c, i=i: _cidr(c, i + 20)),
        tags={{**tags({_py(node.id)}), "Tier": "isolated"}},
    )
    for i in range({azs})
]

{v}_igw = aws.ec2.InternetGateway(
    "{node.id}-igw", vpc_id={v}.id, tags=tags({_py(node.id)})
)

{v}_nat = [
    aws.ec2.NatGateway(
        f"{node.id}-nat-{{i}}",
        allocation_id=aws.ec2.Eip(f"{node.id}-nat-eip-{{i}}", domain="vpc", tags=tags({_py(node.id)})).id,
        subnet_id={v}_public_subnets[i].id,
        opts=pulumi.ResourceOptions(depends_on=[{v}_igw]),
        tags=tags({_py(node.id)}),
    )
    for i in range({nat_count})
]

{v}_flow_logs = aws.cloudwatch.LogGroup(
    "{node.id}-flow-logs",
    name="/aws/vpc/{node.name}/flow-logs",
    retention_in_days=90,
    tags=tags({_py(node.id)}),
)

{v}_flow_role = aws.iam.Role(
    "{node.id}-flow-role",
    name="{node.name}-flow-logs",
    assume_role_policy=json.dumps({{
        "Version": "2012-10-17",
        "Statement": [{{
            "Effect": "Allow",
            "Principal": {{"Service": "vpc-flow-logs.amazonaws.com"}},
            "Action": "sts:AssumeRole",
        }}],
    }}),
    tags=tags({_py(node.id)}),
)

aws.iam.RolePolicy(
    "{node.id}-flow-policy",
    role={v}_flow_role.id,
    policy={v}_flow_logs.arn.apply(lambda arn: json.dumps({{
        "Version": "2012-10-17",
        "Statement": [{{
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
            "Resource": f"{{arn}}:*",
        }}],
    }})),
)

aws.ec2.FlowLog(
    "{node.id}-flow-log",
    vpc_id={v}.id,
    traffic_type="ALL",
    iam_role_arn={v}_flow_role.arn,
    log_destination={v}_flow_logs.arn,
    max_aggregation_interval=60,
    tags=tags({_py(node.id)}),
)

aws.ec2.DefaultSecurityGroup(
    "{node.id}-default-sg",
    vpc_id={v}.id,
    tags={{**tags({_py(node.id)}), "Note": "intentionally empty"}},
)

pulumi.export("{v}_id", {v}.id)
'''

    def _s3(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        expire = int(node.params.get("lifecycle_expire_days", 0))
        lifecycle = ""
        if expire > 0:
            lifecycle = f'''
aws.s3.BucketLifecycleConfigurationV2(
    "{node.id}-lifecycle",
    bucket={v}.id,
    rules=[aws.s3.BucketLifecycleConfigurationV2RuleArgs(
        id="expire",
        status="Enabled",
        filter=aws.s3.BucketLifecycleConfigurationV2RuleFilterArgs(),
        expiration=aws.s3.BucketLifecycleConfigurationV2RuleExpirationArgs(days={expire}),
        noncurrent_version_expiration=aws.s3.BucketLifecycleConfigurationV2RuleNoncurrentVersionExpirationArgs(
            noncurrent_days=30
        ),
    )],
)
'''
        status = "Enabled" if node.params.get("versioning", True) else "Suspended"
        return self._kms(node, "object encryption") + f'''
{v} = aws.s3.BucketV2("{node.id}", bucket="{node.name}", tags=tags({_py(node.id)}))

aws.s3.BucketPublicAccessBlock(
    "{node.id}-pab",
    bucket={v}.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)

aws.s3.BucketVersioningV2(
    "{node.id}-versioning",
    bucket={v}.id,
    versioning_configuration=aws.s3.BucketVersioningV2VersioningConfigurationArgs(status="{status}"),
)

aws.s3.BucketServerSideEncryptionConfigurationV2(
    "{node.id}-sse",
    bucket={v}.id,
    rules=[aws.s3.BucketServerSideEncryptionConfigurationV2RuleArgs(
        apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationV2RuleApplyServerSideEncryptionByDefaultArgs(
            sse_algorithm="aws:kms",
            kms_master_key_id={v}_key.arn,
        ),
        bucket_key_enabled=True,
    )],
)

aws.s3.BucketPolicy(
    "{node.id}-policy",
    bucket={v}.id,
    policy={v}.arn.apply(lambda arn: json.dumps({{
        "Version": "2012-10-17",
        "Statement": [{{
            "Sid": "DenyPlaintextTransport",
            "Effect": "Deny",
            "Principal": "*",
            "Action": "s3:*",
            "Resource": [arn, f"{{arn}}/*"],
            "Condition": {{"Bool": {{"aws:SecureTransport": "false"}}}},
        }}],
    }})),
)
{lifecycle}
pulumi.export("{v}_bucket", {v}.bucket)
'''

    def _dynamodb(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        pk = node.params["partition_key"]
        sk = node.params.get("sort_key") or ""
        attrs = [f'aws.dynamodb.TableAttributeArgs(name={_py(pk)}, type="S")']
        if sk:
            attrs.append(f'aws.dynamodb.TableAttributeArgs(name={_py(sk)}, type="S")')
        extra = ""
        if sk:
            extra += f"    range_key={_py(sk)},\n"
        if node.params.get("ttl_attribute"):
            extra += (
                f'    ttl=aws.dynamodb.TableTtlArgs(attribute_name={_py(node.params["ttl_attribute"])}, enabled=True),\n'
            )
        if node.params.get("stream"):
            extra += '    stream_enabled=True,\n    stream_view_type="NEW_AND_OLD_IMAGES",\n'
        return self._kms(node, "table encryption") + f'''
{v} = aws.dynamodb.Table(
    "{node.id}",
    name="{node.name}",
    billing_mode={_py(node.params.get("billing_mode", "PAY_PER_REQUEST"))},
    hash_key={_py(pk)},
{extra}    attributes=[{", ".join(attrs)}],
    point_in_time_recovery=aws.dynamodb.TablePointInTimeRecoveryArgs(enabled=True),
    server_side_encryption=aws.dynamodb.TableServerSideEncryptionArgs(
        enabled=True, kms_key_arn={v}_key.arn
    ),
    deletion_protection_enabled={plan.production},
    tags=tags({_py(node.id)}),
)

pulumi.export("{v}_name", {v}.name)
'''

    def _sqs(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        fifo = bool(node.params.get("fifo", False))
        suffix = ".fifo" if fifo else ""
        return self._kms(node, "queue encryption") + f'''
{v}_dlq = aws.sqs.Queue(
    "{node.id}-dlq",
    name="{node.name}-dlq{suffix}",
    fifo_queue={fifo},
    kms_master_key_id={v}_key.arn,
    message_retention_seconds=1209600,
    tags={{**tags({_py(node.id)}), "Role": "dlq"}},
)

{v} = aws.sqs.Queue(
    "{node.id}",
    name="{node.name}{suffix}",
    fifo_queue={fifo},
    kms_master_key_id={v}_key.arn,
    visibility_timeout_seconds={int(node.params.get("visibility_timeout_seconds", 30))},
    message_retention_seconds={int(node.params.get("message_retention_seconds", 345600))},
    redrive_policy={v}_dlq.arn.apply(lambda arn: json.dumps({{
        "deadLetterTargetArn": arn,
        "maxReceiveCount": {int(node.params.get("max_receive_count", 5))},
    }})),
    tags=tags({_py(node.id)}),
)

aws.sqs.QueuePolicy(
    "{node.id}-policy",
    queue_url={v}.id,
    policy={v}.arn.apply(lambda arn: json.dumps({{
        "Version": "2012-10-17",
        "Statement": [{{
            "Sid": "DenyPlaintextTransport",
            "Effect": "Deny",
            "Principal": "*",
            "Action": "sqs:SendMessage",
            "Resource": arn,
            "Condition": {{"Bool": {{"aws:SecureTransport": "false"}}}},
        }}],
    }})),
)

pulumi.export("{v}_url", {v}.url)
'''

    def _secret(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        return self._kms(node, "secret encryption") + f'''
{v} = aws.secretsmanager.Secret(
    "{node.id}",
    name="{node.name}",
    description={_py(node.params.get("description", "Managed by OpenCloudOps"))},
    kms_key_id={v}_key.arn,
    tags=tags({_py(node.id)}),
)

# The value is written once and then ignored: IaC owns the secret's existence,
# never its contents. Overwriting a rotated secret from a plan is an outage.
aws.secretsmanager.SecretVersion(
    "{node.id}-value",
    secret_id={v}.id,
    secret_string=json.dumps({{"managed_by": "opencloudops"}}),
    opts=pulumi.ResourceOptions(ignore_changes=["secret_string"]),
)

pulumi.export("{v}_arn", {v}.arn)
'''

    def _rds(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        if vpc is None:
            return (
                f"# {node.id} needs a net.vpc node in the same graph; Pulumi backend does not\n"
                f"# synthesise a VPC reference from configuration. Add one and re-run.\n"
            )
        vv = tf_identifier(vpc.id)
        return self._kms(node, "storage encryption") + f'''
{v}_subnets = aws.rds.SubnetGroup(
    "{node.id}-subnets",
    name="{node.name}",
    subnet_ids=[s.id for s in {vv}_isolated_subnets],
    tags=tags({_py(node.id)}),
)

{v}_sg = aws.ec2.SecurityGroup(
    "{node.id}-sg",
    name="{node.name}",
    description="Database ingress for {plan.service}",
    vpc_id={vv}.id,
    ingress=[aws.ec2.SecurityGroupIngressArgs(
        description="PostgreSQL from inside the VPC only",
        from_port=5432, to_port=5432, protocol="tcp",
        cidr_blocks=[{_py(vpc.params.get("cidr", "10.0.0.0/16"))}],
    )],
    tags=tags({_py(node.id)}),
)

{v}_params = aws.rds.ParameterGroup(
    "{node.id}-params",
    name="{node.name}",
    family="postgres16",
    parameters=[aws.rds.ParameterGroupParameterArgs(name="rds.force_ssl", value="1")],
    tags=tags({_py(node.id)}),
)

{v} = aws.rds.Instance(
    "{node.id}",
    identifier="{node.name}",
    engine="postgres",
    engine_version={_py(node.params.get("engine_version", "16.4"))},
    instance_class={_py(node.params.get("instance_class", "db.t4g.medium"))},
    allocated_storage={int(node.params.get("allocated_storage_gb", 50))},
    max_allocated_storage={int(node.params.get("allocated_storage_gb", 50)) * 4},
    storage_type="gp3",
    storage_encrypted=True,
    kms_key_id={v}_key.arn,
    db_name={_py(node.params.get("database_name", "appdb"))},
    username="occ_admin",
    manage_master_user_password=True,
    iam_database_authentication_enabled=True,
    db_subnet_group_name={v}_subnets.name,
    vpc_security_group_ids=[{v}_sg.id],
    parameter_group_name={v}_params.name,
    publicly_accessible=False,
    multi_az={bool(node.params.get("multi_az", False))},
    backup_retention_period={int(node.params.get("backup_retention_days", 7))},
    copy_tags_to_snapshot=True,
    deletion_protection={plan.production},
    skip_final_snapshot={not plan.production},
    enabled_cloudwatch_logs_exports=["postgresql", "upgrade"],
    performance_insights_enabled=True,
    auto_minor_version_upgrade=True,
    tags=tags({_py(node.id)}),
)

pulumi.export("{v}_endpoint", {v}.address)
'''

    def _lambda(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        vpc_block = ""
        sg = ""
        if vpc is not None:
            vv = tf_identifier(vpc.id)
            sg = f'''{v}_sg = aws.ec2.SecurityGroup(
    "{node.id}-sg",
    name="{node.name}",
    description="Egress for {node.id}",
    vpc_id={vv}.id,
    egress=[aws.ec2.SecurityGroupEgressArgs(
        description="HTTPS to AWS service endpoints inside the VPC",
        from_port=443, to_port=443, protocol="tcp",
        cidr_blocks=[{_py(vpc.params.get("cidr", "10.0.0.0/16"))}],
    )],
    tags=tags({_py(node.id)}),
)

'''
            vpc_block = f'''    vpc_config=aws.lambda_.FunctionVpcConfigArgs(
        subnet_ids=[s.id for s in {vv}_private_subnets],
        security_group_ids=[{v}_sg.id],
    ),
'''
        reserved = int(node.params.get("reserved_concurrency", 0))
        reserved_line = (
            f"    reserved_concurrent_executions={reserved},\n" if reserved > 0 else ""
        )
        return self._kms(node, "environment encryption") + f'''
{sg}{v}_logs = aws.cloudwatch.LogGroup(
    "{node.id}-logs",
    name="/aws/lambda/{node.name}",
    retention_in_days=30,
    kms_key_id={v}_key.arn,
    tags=tags({_py(node.id)}),
)

{v}_dlq = aws.sqs.Queue(
    "{node.id}-dlq",
    name="{node.name}-dlq",
    kms_master_key_id={v}_key.arn,
    tags={{**tags({_py(node.id)}), "Role": "dlq"}},
)

{v}_role = aws.iam.Role(
    "{node.id}-role",
    name="{node.name}",
    assume_role_policy=json.dumps({{
        "Version": "2012-10-17",
        "Statement": [{{
            "Effect": "Allow",
            "Principal": {{"Service": "lambda.amazonaws.com"}},
            "Action": "sts:AssumeRole",
        }}],
    }}),
    tags=tags({_py(node.id)}),
)

aws.iam.RolePolicy(
    "{node.id}-policy",
    role={v}_role.id,
    policy=pulumi.Output.all({v}_logs.arn, {v}_dlq.arn, {v}_key.arn).apply(
        lambda a: json.dumps({{
            "Version": "2012-10-17",
            "Statement": [
                {{"Effect": "Allow",
                 "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                 "Resource": f"{{a[0]}}:*"}},
                {{"Effect": "Allow", "Action": ["sqs:SendMessage"], "Resource": a[1]}},
                {{"Effect": "Allow", "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": a[2]}},
            ],
        }})
    ),
)

aws.iam.RolePolicyAttachment(
    "{node.id}-vpc-access",
    role={v}_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
)

{v} = aws.lambda_.Function(
    "{node.id}",
    name="{node.name}",
    role={v}_role.arn,
    runtime={_py(node.params.get("runtime", "python3.12"))},
    handler={_py(node.params.get("handler", "app.handler"))},
    memory_size={int(node.params.get("memory_mb", 512))},
    timeout={int(node.params.get("timeout_seconds", 30))},
{reserved_line}    code=pulumi.FileArchive(f"./src/{node.id}"),
    kms_key_arn={v}_key.arn,
{vpc_block}    dead_letter_config=aws.lambda_.FunctionDeadLetterConfigArgs(target_arn={v}_dlq.arn),
    tracing_config=aws.lambda_.FunctionTracingConfigArgs(mode="Active"),
    environment=aws.lambda_.FunctionEnvironmentArgs(
        variables={{"ENVIRONMENT": ENVIRONMENT, "SERVICE": SERVICE}}
    ),
    opts=pulumi.ResourceOptions(depends_on=[{v}_logs]),
    tags=tags({_py(node.id)}),
)

pulumi.export("{v}_arn", {v}.arn)
'''

    def _ecs(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        if vpc is None:
            return (
                f"# {node.id} needs a net.vpc node in the same graph; add one and re-run.\n"
            )
        vv = tf_identifier(vpc.id)
        cidr = vpc.params.get("cidr", "10.0.0.0/16")
        port = int(node.params.get("container_port", 8080))
        image_expr = (
            _py(node.params["image"])
            if node.params.get("image")
            else f'config.require("{v}Image")'
        )
        health = node.params.get("health_check_path", "/health")
        return f'''{v}_cluster = aws.ecs.Cluster(
    "{node.id}-cluster",
    name="{node.name}-cluster",
    settings=[aws.ecs.ClusterSettingArgs(name="containerInsights", value="enabled")],
    tags=tags({_py(node.id)}),
)

{v}_logs = aws.cloudwatch.LogGroup(
    "{node.id}-logs", name="/ecs/{node.name}", retention_in_days=30, tags=tags({_py(node.id)})
)

{v}_sg = aws.ec2.SecurityGroup(
    "{node.id}-sg",
    name="{node.name}",
    description="Task networking for {node.id}",
    vpc_id={vv}.id,
    ingress=[aws.ec2.SecurityGroupIngressArgs(
        description="Application traffic from inside the VPC",
        from_port={port}, to_port={port}, protocol="tcp", cidr_blocks=[{_py(cidr)}],
    )],
    egress=[aws.ec2.SecurityGroupEgressArgs(
        description="HTTPS egress to AWS service endpoints",
        from_port=443, to_port=443, protocol="tcp", cidr_blocks=[{_py(cidr)}],
    )],
    tags=tags({_py(node.id)}),
)

{v}_exec_role = aws.iam.Role(
    "{node.id}-exec-role",
    name="{node.name}-exec",
    assume_role_policy=json.dumps({{
        "Version": "2012-10-17",
        "Statement": [{{
            "Effect": "Allow",
            "Principal": {{"Service": "ecs-tasks.amazonaws.com"}},
            "Action": "sts:AssumeRole",
        }}],
    }}),
    tags=tags({_py(node.id)}),
)

aws.iam.RolePolicyAttachment(
    "{node.id}-exec-attach",
    role={v}_exec_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

{v}_task_role = aws.iam.Role(
    "{node.id}-task-role",
    name="{node.name}-task",
    assume_role_policy=json.dumps({{
        "Version": "2012-10-17",
        "Statement": [{{
            "Effect": "Allow",
            "Principal": {{"Service": "ecs-tasks.amazonaws.com"}},
            "Action": "sts:AssumeRole",
        }}],
    }}),
    tags=tags({_py(node.id)}),
)

aws.iam.RolePolicy(
    "{node.id}-task-policy",
    role={v}_task_role.id,
    policy={v}_logs.arn.apply(lambda arn: json.dumps({{
        "Version": "2012-10-17",
        "Statement": [{{
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": f"{{arn}}:*",
        }}],
    }})),
)

{v}_task = aws.ecs.TaskDefinition(
    "{node.id}-task",
    family="{node.name}",
    requires_compatibilities=["FARGATE"],
    network_mode="awsvpc",
    cpu="{int(node.params.get("cpu", 512))}",
    memory="{int(node.params.get("memory_mb", 1024))}",
    execution_role_arn={v}_exec_role.arn,
    task_role_arn={v}_task_role.arn,
    container_definitions=pulumi.Output.all({v}_logs.name).apply(
        lambda a: json.dumps([{{
            "name": {_py(node.id)},
            "image": {image_expr},
            "essential": True,
            "readonlyRootFilesystem": True,
            "portMappings": [{{"containerPort": {port}, "protocol": "tcp"}}],
            "environment": [
                {{"name": "ENVIRONMENT", "value": ENVIRONMENT}},
                {{"name": "SERVICE", "value": SERVICE}},
            ],
            "healthCheck": {{
                "command": ["CMD-SHELL", "curl -fsS http://localhost:{port}{health} || exit 1"],
                "interval": 30, "timeout": 5, "retries": 3, "startPeriod": 20,
            }},
            "logConfiguration": {{
                "logDriver": "awslogs",
                "options": {{
                    "awslogs-group": a[0],
                    "awslogs-region": {_py(plan.region)},
                    "awslogs-stream-prefix": {_py(node.id)},
                }},
            }},
        }}])
    ),
    tags=tags({_py(node.id)}),
)

{v} = aws.ecs.Service(
    "{node.id}",
    name="{node.name}",
    cluster={v}_cluster.id,
    task_definition={v}_task.arn,
    desired_count={int(node.params.get("desired_count", 2))},
    launch_type="FARGATE",
    enable_execute_command=False,
    deployment_circuit_breaker=aws.ecs.ServiceDeploymentCircuitBreakerArgs(enable=True, rollback=True),
    network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
        subnets=[s.id for s in {vv}_private_subnets],
        security_groups=[{v}_sg.id],
        assign_public_ip=False,
    ),
    tags=tags({_py(node.id)}),
)

pulumi.export("{v}_service", {v}.name)
'''

    def _observability(self, node: ResolvedNode, plan: ResolvedPlan) -> str:
        v = tf_identifier(node.id)
        alarms = []
        for target in plan.nodes:
            t = tf_identifier(target.id)
            if target.type is NodeType.container_service:
                alarms.append(f'''
aws.cloudwatch.MetricAlarm(
    "{node.id}-{target.id}-cpu",
    name="{target.name}-cpu-high",
    namespace="AWS/ECS",
    metric_name="CPUUtilization",
    statistic="Average",
    period=300,
    evaluation_periods=3,
    threshold=85,
    comparison_operator="GreaterThanThreshold",
    treat_missing_data="notBreaching",
    alarm_description="Sustained CPU pressure on {target.id}",
    alarm_actions=[{v}_topic.arn],
    dimensions={{"ClusterName": {t}_cluster.name, "ServiceName": {t}.name}},
    tags=tags({_py(node.id)}),
)''')
            if target.type is NodeType.function:
                alarms.append(f'''
aws.cloudwatch.MetricAlarm(
    "{node.id}-{target.id}-errors",
    name="{target.name}-errors",
    namespace="AWS/Lambda",
    metric_name="Errors",
    statistic="Sum",
    period=300,
    evaluation_periods=2,
    threshold=1,
    comparison_operator="GreaterThanThreshold",
    treat_missing_data="notBreaching",
    alarm_description="{target.id} is returning errors",
    alarm_actions=[{v}_topic.arn],
    dimensions={{"FunctionName": {t}.name}},
    tags=tags({_py(node.id)}),
)''')
        return self._kms(node, "log and topic encryption") + f'''
{v}_topic = aws.sns.Topic(
    "{node.id}-topic",
    name="{node.name}",
    kms_master_key_id={v}_key.arn,
    tags=tags({_py(node.id)}),
)

if ALARM_EMAIL:
    aws.sns.TopicSubscription(
        "{node.id}-subscription",
        topic={v}_topic.arn,
        protocol="email",
        endpoint=ALARM_EMAIL,
    )

{v}_logs = aws.cloudwatch.LogGroup(
    "{node.id}-logs",
    name="/opencloudops/{plan.service}/{node.id}",
    retention_in_days={int(node.params.get("log_retention_days", 30))},
    kms_key_id={v}_key.arn,
    tags=tags({_py(node.id)}),
)
{"".join(alarms)}
'''
