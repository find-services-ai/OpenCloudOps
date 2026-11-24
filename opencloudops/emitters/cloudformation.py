"""AWS CloudFormation backend.

Built as Python data structures and serialised to YAML, rather than assembled
from strings: a template is a document with strict shape rules, and building it
as text is how you get a stack that fails at CreateStack with an unhelpful
message. Emitting the structure means a malformed template fails here, in a
unit test, instead of in a change set.
"""

from __future__ import annotations

from typing import Any

import yaml

from opencloudops.emitters.base import Emitter
from opencloudops.naming import logical_id
from opencloudops.spec import NodeType, PlatformContract, ResolvedNode, ResolvedPlan


def _Ref(name: str) -> dict[str, Any]:
    """The ``Ref`` intrinsic.

    A plain dict, not a dict subclass: PyYAML's SafeDumper has no representer
    for subclasses and raises rather than falling back to the mapping rule.
    """
    return {"Ref": name}


def _getatt(resource: str, attribute: str) -> dict[str, Any]:
    return {"Fn::GetAtt": [resource, attribute]}


def _sub(template: str) -> dict[str, Any]:
    return {"Fn::Sub": template}


def _tags(node: ResolvedNode) -> list[dict[str, str]]:
    return [{"Key": k, "Value": v} for k, v in sorted(node.tags.items())]


class CloudFormationEmitter(Emitter):
    id = "cloudformation"
    language = "CloudFormation"
    description = "A single AWS CloudFormation template plus an environment parameter file."

    def emit(self, plan: ResolvedPlan, platform: PlatformContract) -> dict[str, str]:
        resources: dict[str, Any] = {}
        outputs: dict[str, Any] = {}
        parameters: dict[str, Any] = {
            "EnvironmentName": {
                "Type": "String",
                "Default": plan.environment,
                "Description": "Environment this stack belongs to. One parameter, never two.",
            },
            "AlarmEmail": {
                "Type": "String",
                "Default": "",
                "Description": "Alarm destination. Empty skips the subscription.",
            },
        }

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
            render(node, plan, resources, outputs, parameters)

        template: dict[str, Any] = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Description": (
                f"OpenCloudOps: {plan.graph_name} ({plan.environment}). "
                "Generated -- re-run `occ iac` rather than editing."
            ),
            "Parameters": parameters,
            "Conditions": {
                "HasAlarmEmail": {"Fn::Not": [{"Fn::Equals": [_Ref("AlarmEmail"), ""]}]}
            },
            "Resources": resources,
            "Outputs": outputs,
        }

        params_file = [
            {"ParameterKey": "EnvironmentName", "ParameterValue": plan.environment},
            {"ParameterKey": "AlarmEmail", "ParameterValue": ""},
        ]

        files = {
            "template.yaml": self.header(plan)
            + yaml.safe_dump(template, sort_keys=False, width=100),
            f"parameters/{plan.environment}.json": _json_dump(params_file),
            "README.md": self._readme(plan),
        }
        files.update(self.skip_note(plan))
        return files

    def _readme(self, plan: ResolvedPlan) -> str:
        return f"""# {plan.graph_name} -- CloudFormation ({plan.environment})

```bash
aws cloudformation deploy \\
  --template-file template.yaml \\
  --stack-name {plan.service}-{plan.environment} \\
  --parameter-overrides file://parameters/{plan.environment}.json \\
  --capabilities CAPABILITY_NAMED_IAM \\
  --region {plan.region}
```

Review the change set before executing it. Nothing in this repository applies
a stack on your behalf; `release-executor` proposes, a human runs.
"""

    # -- resources ------------------------------------------------------

    def _kms(self, node: ResolvedNode, resources: dict[str, Any], purpose: str) -> str:
        lid = logical_id(node.id, "Key")
        resources[lid] = {
            "Type": "AWS::KMS::Key",
            "Properties": {
                "Description": f"CMK for {node.id} ({purpose})",
                "EnableKeyRotation": True,
                "KeyPolicy": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AccountRoot",
                            "Effect": "Allow",
                            "Principal": {"AWS": _sub("arn:${AWS::Partition}:iam::${AWS::AccountId}:root")},
                            "Action": [
                                "kms:Create*",
                                "kms:Describe*",
                                "kms:Enable*",
                                "kms:List*",
                                "kms:Put*",
                                "kms:Update*",
                                "kms:Revoke*",
                                "kms:Disable*",
                                "kms:Get*",
                                "kms:Delete*",
                                "kms:ScheduleKeyDeletion",
                                "kms:CancelKeyDeletion",
                            ],
                            "Resource": "*",
                        }
                    ],
                },
                "Tags": _tags(node),
            },
        }
        return lid

    def _vpc(self, node, plan, resources, outputs, parameters) -> None:
        lid = logical_id(node.id)
        azs = int(node.params.get("availability_zones", 2))
        resources[lid] = {
            "Type": "AWS::EC2::VPC",
            "Properties": {
                "CidrBlock": node.params.get("cidr", "10.0.0.0/16"),
                "EnableDnsSupport": True,
                "EnableDnsHostnames": True,
                "Tags": _tags(node),
            },
        }
        for tier, offset, public in (("Public", 0, False), ("Private", 10, False), ("Isolated", 20, False)):
            for i in range(azs):
                resources[f"{lid}{tier}Subnet{i + 1}"] = {
                    "Type": "AWS::EC2::Subnet",
                    "Properties": {
                        "VpcId": _Ref(lid),
                        "AvailabilityZone": {"Fn::Select": [i, {"Fn::GetAZs": ""}]},
                        "CidrBlock": {
                            "Fn::Select": [
                                i + offset,
                                {"Fn::Cidr": [_getatt(lid, "CidrBlock"), 30, 8]},
                            ]
                        },
                        "MapPublicIpOnLaunch": public,
                        "Tags": _tags(node) + [{"Key": "Tier", "Value": tier.lower()}],
                    },
                }
        log_group = f"{lid}FlowLogs"
        resources[log_group] = {
            "Type": "AWS::Logs::LogGroup",
            "Properties": {
                "LogGroupName": f"/aws/vpc/{node.name}/flow-logs",
                "RetentionInDays": 90,
            },
        }
        role = f"{lid}FlowLogRole"
        resources[role] = {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "vpc-flow-logs.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                },
                "Policies": [
                    {
                        "PolicyName": "flow-logs",
                        "PolicyDocument": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "logs:CreateLogStream",
                                        "logs:PutLogEvents",
                                        "logs:DescribeLogStreams",
                                    ],
                                    "Resource": _sub(
                                        "${" + log_group + ".Arn}:*"
                                    ),
                                }
                            ],
                        },
                    }
                ],
                "Tags": _tags(node),
            },
        }
        resources[f"{lid}FlowLog"] = {
            "Type": "AWS::EC2::FlowLog",
            "Properties": {
                "ResourceId": _Ref(lid),
                "ResourceType": "VPC",
                "TrafficType": "ALL",
                "DeliverLogsPermissionArn": _getatt(role, "Arn"),
                "LogGroupName": _Ref(log_group),
                "MaxAggregationInterval": 60,
                "Tags": _tags(node),
            },
        }
        outputs[f"{lid}Id"] = {"Description": f"{node.id} VPC id", "Value": _Ref(lid)}

    def _s3(self, node, plan, resources, outputs, parameters) -> None:
        key = self._kms(node, resources, "object encryption")
        lid = logical_id(node.id)
        props: dict[str, Any] = {
            "BucketName": node.name,
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "ServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": _getatt(key, "Arn"),
                        },
                        "BucketKeyEnabled": True,
                    }
                ]
            },
            "VersioningConfiguration": {
                "Status": "Enabled" if node.params.get("versioning", True) else "Suspended"
            },
            "Tags": _tags(node),
        }
        expire = int(node.params.get("lifecycle_expire_days", 0))
        if expire > 0:
            props["LifecycleConfiguration"] = {
                "Rules": [
                    {
                        "Id": "expire",
                        "Status": "Enabled",
                        "ExpirationInDays": expire,
                        "NoncurrentVersionExpirationInDays": 30,
                    }
                ]
            }
        resources[lid] = {"Type": "AWS::S3::Bucket", "Properties": props}
        resources[f"{lid}Policy"] = {
            "Type": "AWS::S3::BucketPolicy",
            "Properties": {
                "Bucket": _Ref(lid),
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "DenyPlaintextTransport",
                            "Effect": "Deny",
                            "Principal": "*",
                            "Action": "s3:*",
                            "Resource": [
                                _getatt(lid, "Arn"),
                                _sub("${" + lid + ".Arn}/*"),
                            ],
                            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                        }
                    ],
                },
            },
        }
        outputs[f"{lid}Name"] = {"Description": f"{node.id} bucket", "Value": _Ref(lid)}

    def _dynamodb(self, node, plan, resources, outputs, parameters) -> None:
        key = self._kms(node, resources, "table encryption")
        lid = logical_id(node.id)
        pk = node.params["partition_key"]
        sk = node.params.get("sort_key") or ""
        attrs = [{"AttributeName": pk, "AttributeType": "S"}]
        schema = [{"AttributeName": pk, "KeyType": "HASH"}]
        if sk:
            attrs.append({"AttributeName": sk, "AttributeType": "S"})
            schema.append({"AttributeName": sk, "KeyType": "RANGE"})
        props: dict[str, Any] = {
            "TableName": node.name,
            "BillingMode": node.params.get("billing_mode", "PAY_PER_REQUEST"),
            "AttributeDefinitions": attrs,
            "KeySchema": schema,
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            "SSESpecification": {
                "SSEEnabled": True,
                "SSEType": "KMS",
                "KMSMasterKeyId": _getatt(key, "Arn"),
            },
            "DeletionProtectionEnabled": plan.production,
            "Tags": _tags(node),
        }
        if node.params.get("ttl_attribute"):
            props["TimeToLiveSpecification"] = {
                "AttributeName": node.params["ttl_attribute"],
                "Enabled": True,
            }
        if node.params.get("stream"):
            props["StreamSpecification"] = {"StreamViewType": "NEW_AND_OLD_IMAGES"}
        resources[lid] = {"Type": "AWS::DynamoDB::Table", "Properties": props}
        outputs[f"{lid}Name"] = {"Description": f"{node.id} table", "Value": _Ref(lid)}

    def _sqs(self, node, plan, resources, outputs, parameters) -> None:
        key = self._kms(node, resources, "queue encryption")
        lid = logical_id(node.id)
        fifo = bool(node.params.get("fifo", False))
        suffix = ".fifo" if fifo else ""
        dlq = f"{lid}Dlq"
        resources[dlq] = {
            "Type": "AWS::SQS::Queue",
            "Properties": {
                "QueueName": f"{node.name}-dlq{suffix}",
                "FifoQueue": fifo or None,
                "KmsMasterKeyId": _getatt(key, "Arn"),
                "MessageRetentionPeriod": 1209600,
                "Tags": _tags(node),
            },
        }
        resources[dlq]["Properties"] = {
            k: v for k, v in resources[dlq]["Properties"].items() if v is not None
        }
        props = {
            "QueueName": f"{node.name}{suffix}",
            "FifoQueue": fifo or None,
            "KmsMasterKeyId": _getatt(key, "Arn"),
            "VisibilityTimeout": int(node.params.get("visibility_timeout_seconds", 30)),
            "MessageRetentionPeriod": int(node.params.get("message_retention_seconds", 345600)),
            "RedrivePolicy": {
                "deadLetterTargetArn": _getatt(dlq, "Arn"),
                "maxReceiveCount": int(node.params.get("max_receive_count", 5)),
            },
            "Tags": _tags(node),
        }
        resources[lid] = {
            "Type": "AWS::SQS::Queue",
            "Properties": {k: v for k, v in props.items() if v is not None},
        }
        outputs[f"{lid}Url"] = {"Description": f"{node.id} queue", "Value": _Ref(lid)}

    def _secret(self, node, plan, resources, outputs, parameters) -> None:
        key = self._kms(node, resources, "secret encryption")
        lid = logical_id(node.id)
        resources[lid] = {
            "Type": "AWS::SecretsManager::Secret",
            "Properties": {
                "Name": node.name,
                "Description": node.params.get("description", "Managed by OpenCloudOps"),
                "KmsKeyId": _getatt(key, "Arn"),
                "GenerateSecretString": {
                    "SecretStringTemplate": '{"managed_by":"opencloudops"}',
                    "GenerateStringKey": "value",
                    "PasswordLength": 32,
                    "ExcludePunctuation": True,
                },
                "Tags": _tags(node),
            },
            "UpdateReplacePolicy": "Retain",
            "DeletionPolicy": "Retain",
        }
        outputs[f"{lid}Arn"] = {"Description": f"{node.id} secret", "Value": _Ref(lid)}

    def _rds(self, node, plan, resources, outputs, parameters) -> None:
        key = self._kms(node, resources, "storage encryption")
        lid = logical_id(node.id)
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        if vpc is None:
            parameters.setdefault(
                "VpcId", {"Type": "AWS::EC2::VPC::Id", "Description": "Existing VPC."}
            )
            parameters.setdefault(
                "IsolatedSubnetIds",
                {"Type": "List<AWS::EC2::Subnet::Id>", "Description": "Isolated subnets."},
            )
            vpc_ref: Any = _Ref("VpcId")
            subnet_ids: Any = _Ref("IsolatedSubnetIds")
            cidr_source: Any = {"CidrIp": "10.0.0.0/8"}
        else:
            vlid = logical_id(vpc.id)
            azs = int(vpc.params.get("availability_zones", 2))
            vpc_ref = _Ref(vlid)
            subnet_ids = [_Ref(f"{vlid}IsolatedSubnet{i + 1}") for i in range(azs)]
            cidr_source = {"CidrIp": vpc.params.get("cidr", "10.0.0.0/16")}

        sg = f"{lid}SecurityGroup"
        resources[sg] = {
            "Type": "AWS::EC2::SecurityGroup",
            "Properties": {
                "GroupDescription": f"Database ingress for {plan.service}",
                "VpcId": vpc_ref,
                "SecurityGroupIngress": [
                    {
                        "Description": "PostgreSQL from inside the VPC only",
                        "IpProtocol": "tcp",
                        "FromPort": 5432,
                        "ToPort": 5432,
                        **cidr_source,
                    }
                ],
                "Tags": _tags(node),
            },
        }
        subnet_group = f"{lid}SubnetGroup"
        resources[subnet_group] = {
            "Type": "AWS::RDS::DBSubnetGroup",
            "Properties": {
                "DBSubnetGroupDescription": f"Isolated subnets for {node.id}",
                "SubnetIds": subnet_ids,
                "Tags": _tags(node),
            },
        }
        param_group = f"{lid}ParameterGroup"
        resources[param_group] = {
            "Type": "AWS::RDS::DBParameterGroup",
            "Properties": {
                "Description": "Force TLS",
                "Family": "postgres16",
                "Parameters": {"rds.force_ssl": "1"},
                "Tags": _tags(node),
            },
        }
        resources[lid] = {
            "Type": "AWS::RDS::DBInstance",
            "DeletionPolicy": "Snapshot" if plan.production else "Delete",
            "UpdateReplacePolicy": "Snapshot" if plan.production else "Delete",
            "Properties": {
                "DBInstanceIdentifier": node.name,
                "Engine": "postgres",
                "EngineVersion": node.params.get("engine_version", "16.4"),
                "DBInstanceClass": node.params.get("instance_class", "db.t4g.medium"),
                "AllocatedStorage": str(int(node.params.get("allocated_storage_gb", 50))),
                "StorageType": "gp3",
                "StorageEncrypted": True,
                "KmsKeyId": _getatt(key, "Arn"),
                "DBName": node.params.get("database_name", "appdb"),
                "MasterUsername": "occ_admin",
                "ManageMasterUserPassword": True,
                "EnableIAMDatabaseAuthentication": True,
                "DBSubnetGroupName": _Ref(subnet_group),
                "DBParameterGroupName": _Ref(param_group),
                "VPCSecurityGroups": [_getatt(sg, "GroupId")],
                "PubliclyAccessible": False,
                "MultiAZ": bool(node.params.get("multi_az", False)),
                "BackupRetentionPeriod": int(node.params.get("backup_retention_days", 7)),
                "CopyTagsToSnapshot": True,
                "DeletionProtection": plan.production,
                "EnableCloudwatchLogsExports": ["postgresql", "upgrade"],
                "EnablePerformanceInsights": True,
                "AutoMinorVersionUpgrade": True,
                "Tags": _tags(node),
            },
        }
        outputs[f"{lid}Endpoint"] = {
            "Description": f"{node.id} endpoint",
            "Value": _getatt(lid, "Endpoint.Address"),
        }

    def _lambda(self, node, plan, resources, outputs, parameters) -> None:
        key = self._kms(node, resources, "environment encryption")
        lid = logical_id(node.id)
        parameters.setdefault(
            f"{lid}CodeS3Bucket",
            {"Type": "String", "Description": f"Bucket holding the {node.id} deployment package."},
        )
        parameters.setdefault(
            f"{lid}CodeS3Key",
            {"Type": "String", "Description": f"Key of the {node.id} deployment package."},
        )
        log_group = f"{lid}LogGroup"
        resources[log_group] = {
            "Type": "AWS::Logs::LogGroup",
            "Properties": {
                "LogGroupName": f"/aws/lambda/{node.name}",
                "RetentionInDays": 30,
                "KmsKeyId": _getatt(key, "Arn"),
            },
        }
        dlq = f"{lid}Dlq"
        resources[dlq] = {
            "Type": "AWS::SQS::Queue",
            "Properties": {
                "QueueName": f"{node.name}-dlq",
                "KmsMasterKeyId": _getatt(key, "Arn"),
                "Tags": _tags(node),
            },
        }
        role = f"{lid}Role"
        resources[role] = {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "RoleName": node.name,
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                },
                "ManagedPolicyArns": [
                    _sub(
                        "arn:${AWS::Partition}:iam::aws:policy/service-role/"
                        "AWSLambdaVPCAccessExecutionRole"
                    )
                ],
                "Policies": [
                    {
                        "PolicyName": "runtime",
                        "PolicyDocument": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                                    "Resource": _sub("${" + log_group + ".Arn}:*"),
                                },
                                {
                                    "Effect": "Allow",
                                    "Action": ["sqs:SendMessage"],
                                    "Resource": _getatt(dlq, "Arn"),
                                },
                                {
                                    "Effect": "Allow",
                                    "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
                                    "Resource": _getatt(key, "Arn"),
                                },
                            ],
                        },
                    }
                ],
                "Tags": _tags(node),
            },
        }
        props: dict[str, Any] = {
            "FunctionName": node.name,
            "Role": _getatt(role, "Arn"),
            "Runtime": node.params.get("runtime", "python3.12"),
            "Handler": node.params.get("handler", "app.handler"),
            "MemorySize": int(node.params.get("memory_mb", 512)),
            "Timeout": int(node.params.get("timeout_seconds", 30)),
            "KmsKeyArn": _getatt(key, "Arn"),
            "Code": {
                "S3Bucket": _Ref(f"{lid}CodeS3Bucket"),
                "S3Key": _Ref(f"{lid}CodeS3Key"),
            },
            "DeadLetterConfig": {"TargetArn": _getatt(dlq, "Arn")},
            "TracingConfig": {"Mode": "Active"},
            "Environment": {
                "Variables": {"ENVIRONMENT": _Ref("EnvironmentName"), "SERVICE": plan.service}
            },
            "Tags": _tags(node),
        }
        reserved = int(node.params.get("reserved_concurrency", 0))
        if reserved > 0:
            props["ReservedConcurrentExecutions"] = reserved
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        if vpc is not None:
            vlid = logical_id(vpc.id)
            azs = int(vpc.params.get("availability_zones", 2))
            sg = f"{lid}SecurityGroup"
            resources[sg] = {
                "Type": "AWS::EC2::SecurityGroup",
                "Properties": {
                    "GroupDescription": f"Egress for {node.id}",
                    "VpcId": _Ref(vlid),
                    "SecurityGroupEgress": [
                        {
                            "Description": "HTTPS to AWS service endpoints inside the VPC",
                            "IpProtocol": "tcp",
                            "FromPort": 443,
                            "ToPort": 443,
                            "CidrIp": vpc.params.get("cidr", "10.0.0.0/16"),
                        }
                    ],
                    "Tags": _tags(node),
                },
            }
            props["VpcConfig"] = {
                "SecurityGroupIds": [_getatt(sg, "GroupId")],
                "SubnetIds": [_Ref(f"{vlid}PrivateSubnet{i + 1}") for i in range(azs)],
            }
        resources[lid] = {"Type": "AWS::Lambda::Function", "Properties": props, "DependsOn": [log_group]}
        outputs[f"{lid}Arn"] = {"Description": f"{node.id} function", "Value": _getatt(lid, "Arn")}

    def _ecs(self, node, plan, resources, outputs, parameters) -> None:
        lid = logical_id(node.id)
        port = int(node.params.get("container_port", 8080))
        if not node.params.get("image"):
            parameters.setdefault(
                f"{lid}Image",
                {"Type": "String", "Description": f"Container image URI:tag for {node.id}."},
            )
            image: Any = _Ref(f"{lid}Image")
        else:
            image = node.params["image"]

        cluster = f"{lid}Cluster"
        resources[cluster] = {
            "Type": "AWS::ECS::Cluster",
            "Properties": {
                "ClusterName": f"{node.name}-cluster",
                "ClusterSettings": [{"Name": "containerInsights", "Value": "enabled"}],
                "Tags": _tags(node),
            },
        }
        log_group = f"{lid}LogGroup"
        resources[log_group] = {
            "Type": "AWS::Logs::LogGroup",
            "Properties": {"LogGroupName": f"/ecs/{node.name}", "RetentionInDays": 30},
        }
        vpc = next((n for n in plan.by_type(NodeType.network)), None)
        if vpc is None:
            parameters.setdefault("VpcId", {"Type": "AWS::EC2::VPC::Id", "Description": "Existing VPC."})
            parameters.setdefault(
                "PrivateSubnetIds",
                {"Type": "List<AWS::EC2::Subnet::Id>", "Description": "Private subnets."},
            )
            vpc_ref: Any = _Ref("VpcId")
            subnets: Any = _Ref("PrivateSubnetIds")
            cidr = "10.0.0.0/8"
        else:
            vlid = logical_id(vpc.id)
            azs = int(vpc.params.get("availability_zones", 2))
            vpc_ref = _Ref(vlid)
            subnets = [_Ref(f"{vlid}PrivateSubnet{i + 1}") for i in range(azs)]
            cidr = vpc.params.get("cidr", "10.0.0.0/16")

        sg = f"{lid}SecurityGroup"
        resources[sg] = {
            "Type": "AWS::EC2::SecurityGroup",
            "Properties": {
                "GroupDescription": f"Task networking for {node.id}",
                "VpcId": vpc_ref,
                "SecurityGroupIngress": [
                    {
                        "Description": "Application traffic from inside the VPC",
                        "IpProtocol": "tcp",
                        "FromPort": port,
                        "ToPort": port,
                        "CidrIp": cidr,
                    }
                ],
                "SecurityGroupEgress": [
                    {
                        "Description": "HTTPS egress to AWS service endpoints",
                        "IpProtocol": "tcp",
                        "FromPort": 443,
                        "ToPort": 443,
                        "CidrIp": cidr,
                    }
                ],
                "Tags": _tags(node),
            },
        }
        exec_role = f"{lid}ExecutionRole"
        resources[exec_role] = {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "RoleName": f"{node.name}-exec",
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                },
                "ManagedPolicyArns": [
                    _sub(
                        "arn:${AWS::Partition}:iam::aws:policy/service-role/"
                        "AmazonECSTaskExecutionRolePolicy"
                    )
                ],
                "Tags": _tags(node),
            },
        }
        task_role = f"{lid}TaskRole"
        resources[task_role] = {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "RoleName": f"{node.name}-task",
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                },
                "Policies": [
                    {
                        "PolicyName": "runtime",
                        "PolicyDocument": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                                    "Resource": _sub("${" + log_group + ".Arn}:*"),
                                }
                            ],
                        },
                    }
                ],
                "Tags": _tags(node),
            },
        }
        task_def = f"{lid}TaskDefinition"
        resources[task_def] = {
            "Type": "AWS::ECS::TaskDefinition",
            "Properties": {
                "Family": node.name,
                "RequiresCompatibilities": ["FARGATE"],
                "NetworkMode": "awsvpc",
                "Cpu": str(int(node.params.get("cpu", 512))),
                "Memory": str(int(node.params.get("memory_mb", 1024))),
                "ExecutionRoleArn": _getatt(exec_role, "Arn"),
                "TaskRoleArn": _getatt(task_role, "Arn"),
                "ContainerDefinitions": [
                    {
                        "Name": node.id,
                        "Image": image,
                        "Essential": True,
                        "ReadonlyRootFilesystem": True,
                        "PortMappings": [{"ContainerPort": port, "Protocol": "tcp"}],
                        "Environment": [
                            {"Name": "ENVIRONMENT", "Value": _Ref("EnvironmentName")},
                            {"Name": "SERVICE", "Value": plan.service},
                        ],
                        "HealthCheck": {
                            "Command": [
                                "CMD-SHELL",
                                f"curl -fsS http://localhost:{port}"
                                f"{node.params.get('health_check_path', '/health')} || exit 1",
                            ],
                            "Interval": 30,
                            "Timeout": 5,
                            "Retries": 3,
                            "StartPeriod": 20,
                        },
                        "LogConfiguration": {
                            "LogDriver": "awslogs",
                            "Options": {
                                "awslogs-group": _Ref(log_group),
                                "awslogs-region": _Ref("AWS::Region"),
                                "awslogs-stream-prefix": node.id,
                            },
                        },
                    }
                ],
                "Tags": _tags(node),
            },
        }
        resources[f"{lid}Service"] = {
            "Type": "AWS::ECS::Service",
            "Properties": {
                "ServiceName": node.name,
                "Cluster": _Ref(cluster),
                "TaskDefinition": _Ref(task_def),
                "DesiredCount": int(node.params.get("desired_count", 2)),
                "LaunchType": "FARGATE",
                "EnableExecuteCommand": False,
                "DeploymentConfiguration": {
                    "DeploymentCircuitBreaker": {"Enable": True, "Rollback": True}
                },
                "NetworkConfiguration": {
                    "AwsvpcConfiguration": {
                        "AssignPublicIp": "DISABLED",
                        "SecurityGroups": [_getatt(sg, "GroupId")],
                        "Subnets": subnets,
                    }
                },
                "Tags": _tags(node),
            },
        }
        outputs[f"{lid}ServiceName"] = {
            "Description": f"{node.id} service",
            "Value": _getatt(f"{lid}Service", "Name"),
        }

    def _observability(self, node, plan, resources, outputs, parameters) -> None:
        key = self._kms(node, resources, "log and topic encryption")
        lid = logical_id(node.id)
        topic = f"{lid}Topic"
        resources[topic] = {
            "Type": "AWS::SNS::Topic",
            "Properties": {
                "TopicName": node.name,
                "KmsMasterKeyId": _getatt(key, "Arn"),
                "Tags": _tags(node),
            },
        }
        resources[f"{lid}Subscription"] = {
            "Type": "AWS::SNS::Subscription",
            "Condition": "HasAlarmEmail",
            "Properties": {
                "TopicArn": _Ref(topic),
                "Protocol": "email",
                "Endpoint": _Ref("AlarmEmail"),
            },
        }
        resources[f"{lid}LogGroup"] = {
            "Type": "AWS::Logs::LogGroup",
            "Properties": {
                "LogGroupName": f"/opencloudops/{plan.service}/{node.id}",
                "RetentionInDays": int(node.params.get("log_retention_days", 30)),
                "KmsKeyId": _getatt(key, "Arn"),
            },
        }
        for target in plan.nodes:
            tlid = logical_id(target.id)
            if target.type is NodeType.container_service:
                resources[f"{lid}{tlid}CpuAlarm"] = {
                    "Type": "AWS::CloudWatch::Alarm",
                    "Properties": {
                        "AlarmName": f"{target.name}-cpu-high",
                        "AlarmDescription": f"Sustained CPU pressure on {target.id}",
                        "Namespace": "AWS/ECS",
                        "MetricName": "CPUUtilization",
                        "Statistic": "Average",
                        "Period": 300,
                        "EvaluationPeriods": 3,
                        "Threshold": 85,
                        "ComparisonOperator": "GreaterThanThreshold",
                        "TreatMissingData": "notBreaching",
                        "AlarmActions": [_Ref(topic)],
                        "Dimensions": [
                            {"Name": "ClusterName", "Value": _Ref(f"{tlid}Cluster")},
                            {"Name": "ServiceName", "Value": _getatt(f"{tlid}Service", "Name")},
                        ],
                    },
                }
            if target.type is NodeType.function:
                resources[f"{lid}{tlid}ErrorAlarm"] = {
                    "Type": "AWS::CloudWatch::Alarm",
                    "Properties": {
                        "AlarmName": f"{target.name}-errors",
                        "AlarmDescription": f"{target.id} is returning errors",
                        "Namespace": "AWS/Lambda",
                        "MetricName": "Errors",
                        "Statistic": "Sum",
                        "Period": 300,
                        "EvaluationPeriods": 2,
                        "Threshold": 1,
                        "ComparisonOperator": "GreaterThanThreshold",
                        "TreatMissingData": "notBreaching",
                        "AlarmActions": [_Ref(topic)],
                        "Dimensions": [{"Name": "FunctionName", "Value": _Ref(tlid)}],
                    },
                }


def _json_dump(data: object) -> str:
    import json

    return json.dumps(data, indent=2) + "\n"
