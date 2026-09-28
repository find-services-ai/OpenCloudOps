"""Kubernetes backend.

Only part of a cloud plan has a Kubernetes projection: a container service
becomes a Deployment plus a Service, a secret becomes an ExternalSecret
reference, and observability becomes a ServiceMonitor and PrometheusRule. A
VPC, an RDS instance or an S3 bucket do not -- and the honest thing is to say
so rather than invent an operator CRD nobody has installed.

So this backend declares those node types unsupported, and ``Emitter.skip_note``
writes an ``UNSUPPORTED.md`` naming every node it did not render. A reviewer
reading only the manifests would otherwise believe the plan was fully covered.

Secrets are emitted as External Secrets Operator ``ExternalSecret`` objects
that *reference* the cloud secret. A literal Kubernetes Secret is base64, not
encryption, and would put the value in git.
"""

from __future__ import annotations

import yaml

from opencloudops.emitters.base import Emitter
from opencloudops.spec import NodeType, PlatformContract, ResolvedNode, ResolvedPlan


class KubernetesEmitter(Emitter):
    id = "kubernetes"
    language = "Kubernetes"
    description = "Deployment, Service, HPA, NetworkPolicy, ExternalSecret and Prometheus rules."
    unsupported = (
        NodeType.network,
        NodeType.object_store,
        NodeType.table,
        NodeType.relational,
        NodeType.queue,
        NodeType.function,
    )

    def emit(self, plan: ResolvedPlan, platform: PlatformContract) -> dict[str, str]:
        files: dict[str, str] = {}
        namespace = f"{plan.service}-{plan.environment}"
        resources: list[str] = []

        files["namespace.yaml"] = self._doc(self._namespace(plan, namespace))
        resources.append("namespace.yaml")

        for node in self.supported_nodes(plan):
            if node.type is NodeType.container_service:
                path = f"{node.id}/deployment.yaml"
                files[path] = self._doc(
                    self._deployment(node, plan, namespace),
                    self._service(node, plan, namespace),
                    self._hpa(node, plan, namespace),
                    self._network_policy(node, plan, namespace),
                )
                resources.append(path)
            elif node.type is NodeType.secret:
                path = f"{node.id}/externalsecret.yaml"
                files[path] = self._doc(self._external_secret(node, plan, namespace))
                resources.append(path)
            elif node.type is NodeType.observability:
                path = f"{node.id}/monitoring.yaml"
                docs = [self._service_monitor(node, plan, namespace)]
                rule = self._prometheus_rule(node, plan, namespace)
                if rule:
                    docs.append(rule)
                files[path] = self._doc(*docs)
                resources.append(path)

        files["kustomization.yaml"] = self._doc(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "namespace": namespace,
                "commonLabels": self._labels(plan),
                "resources": resources,
            }
        )
        banner = self.header(plan)
        files = {path: banner + body for path, body in files.items()}
        files.update(self.skip_note(plan))
        return files

    # -- helpers --------------------------------------------------------

    def _doc(self, *docs: dict) -> str:
        rendered = [yaml.safe_dump(d, sort_keys=False, width=100) for d in docs if d]
        return "---\n".join(rendered)

    def _labels(self, plan: ResolvedPlan) -> dict[str, str]:
        return {
            "app.kubernetes.io/part-of": plan.service,
            "app.kubernetes.io/managed-by": "opencloudops",
            "opencloudops.dev/environment": plan.environment,
        }

    def _annotations(self, node: ResolvedNode) -> dict[str, str]:
        # Cloud tags have no Kubernetes equivalent, so they are carried as
        # annotations -- cost tooling and the SRE agent both read them back.
        return {f"opencloudops.dev/{k.lower()}": v for k, v in sorted(node.tags.items())}

    def _namespace(self, plan: ResolvedPlan, namespace: str) -> dict:
        return {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    **self._labels(plan),
                    # Baseline pod security. `restricted` is the point of the
                    # exercise: a namespace without it silently permits root.
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                },
            },
        }

    def _deployment(self, node: ResolvedNode, plan: ResolvedPlan, namespace: str) -> dict:
        port = int(node.params.get("container_port", 8080))
        health = node.params.get("health_check_path", "/health")
        cpu = int(node.params.get("cpu", 512))
        memory = int(node.params.get("memory_mb", 1024))
        selector = {"app.kubernetes.io/name": node.id, "app.kubernetes.io/instance": plan.service}
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": node.name,
                "namespace": namespace,
                "labels": {**self._labels(plan), **selector},
                "annotations": self._annotations(node),
            },
            "spec": {
                "replicas": int(node.params.get("desired_count", 2)),
                "selector": {"matchLabels": selector},
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                },
                "template": {
                    "metadata": {"labels": {**self._labels(plan), **selector}},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 10001,
                            "fsGroup": 10001,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": node.id,
                                "image": node.params.get("image") or f"REPLACE/{node.id}:TAG",
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [{"containerPort": port, "name": "http"}],
                                "env": [
                                    {"name": "ENVIRONMENT", "value": plan.environment},
                                    {"name": "SERVICE", "value": plan.service},
                                ],
                                "resources": {
                                    "requests": {"cpu": f"{cpu}m", "memory": f"{memory}Mi"},
                                    "limits": {"memory": f"{memory}Mi"},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": health, "port": "http"},
                                    "initialDelaySeconds": 20,
                                    "periodSeconds": 30,
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": health, "port": "http"},
                                    "initialDelaySeconds": 5,
                                    "periodSeconds": 10,
                                },
                                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                            }
                        ],
                        "volumes": [{"name": "tmp", "emptyDir": {}}],
                    },
                },
            },
        }

    def _service(self, node: ResolvedNode, plan: ResolvedPlan, namespace: str) -> dict:
        port = int(node.params.get("container_port", 8080))
        selector = {"app.kubernetes.io/name": node.id, "app.kubernetes.io/instance": plan.service}
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": node.name,
                "namespace": namespace,
                "labels": {**self._labels(plan), **selector},
            },
            "spec": {
                "type": "ClusterIP",
                "selector": selector,
                "ports": [{"name": "http", "port": 80, "targetPort": port}],
            },
        }

    def _hpa(self, node: ResolvedNode, plan: ResolvedPlan, namespace: str) -> dict:
        replicas = int(node.params.get("desired_count", 2))
        return {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": node.name, "namespace": namespace},
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": node.name,
                },
                "minReplicas": replicas,
                "maxReplicas": max(replicas * 4, 4),
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {"type": "Utilization", "averageUtilization": 70},
                        },
                    }
                ],
            },
        }

    def _network_policy(self, node: ResolvedNode, plan: ResolvedPlan, namespace: str) -> dict:
        """Default-deny with named exceptions. A namespace without one of these
        is flat: any compromised pod anywhere can reach this service."""
        port = int(node.params.get("container_port", 8080))
        selector = {"app.kubernetes.io/name": node.id, "app.kubernetes.io/instance": plan.service}
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": node.name, "namespace": namespace},
            "spec": {
                "podSelector": {"matchLabels": selector},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespace}}}],
                        "ports": [{"protocol": "TCP", "port": port}],
                    }
                ],
                "egress": [
                    {
                        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
                        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
                    },
                    {"ports": [{"protocol": "TCP", "port": 443}]},
                ],
            },
        }

    def _external_secret(self, node: ResolvedNode, plan: ResolvedPlan, namespace: str) -> dict:
        return {
            "apiVersion": "external-secrets.io/v1beta1",
            "kind": "ExternalSecret",
            "metadata": {
                "name": node.name,
                "namespace": namespace,
                "annotations": self._annotations(node),
            },
            "spec": {
                "refreshInterval": "1h",
                "secretStoreRef": {"name": "aws-secretsmanager", "kind": "ClusterSecretStore"},
                "target": {"name": node.name, "creationPolicy": "Owner"},
                "dataFrom": [{"extract": {"key": node.name}}],
            },
        }

    def _service_monitor(self, node: ResolvedNode, plan: ResolvedPlan, namespace: str) -> dict:
        return {
            "apiVersion": "monitoring.coreos.com/v1",
            "kind": "ServiceMonitor",
            "metadata": {
                "name": node.name,
                "namespace": namespace,
                "labels": self._labels(plan),
            },
            "spec": {
                "selector": {"matchLabels": {"app.kubernetes.io/instance": plan.service}},
                "endpoints": [{"port": "http", "path": "/metrics", "interval": "30s"}],
            },
        }

    def _prometheus_rule(self, node: ResolvedNode, plan: ResolvedPlan, namespace: str) -> dict | None:
        services = plan.by_type(NodeType.container_service)
        if not services:
            return None
        rules = []
        for target in services:
            availability = target.slo.availability_pct
            budget = round((100 - availability) / 100, 6)
            rules.append(
                {
                    "alert": f"{target.id}-error-budget-burn",
                    "expr": (
                        f'sum(rate(http_requests_total{{service="{target.id}",status=~"5.."}}[5m]))'
                        f' / sum(rate(http_requests_total{{service="{target.id}"}}[5m])) > {budget * 14:.6f}'
                    ),
                    "for": "5m",
                    "labels": {"severity": "page", "service": target.id},
                    "annotations": {
                        "summary": f"{target.id} is burning its error budget 14x faster than sustainable",
                        "runbook": f"runbooks/{target.id}.md",
                    },
                }
            )
            if target.slo.latency_p95_ms:
                rules.append(
                    {
                        "alert": f"{target.id}-latency-p95",
                        "expr": (
                            "histogram_quantile(0.95, sum(rate("
                            f'http_request_duration_seconds_bucket{{service="{target.id}"}}[5m])) by (le))'
                            f" > {target.slo.latency_p95_ms / 1000:.3f}"
                        ),
                        "for": "10m",
                        "labels": {"severity": "ticket", "service": target.id},
                        "annotations": {
                            "summary": f"{target.id} p95 latency is above its {target.slo.latency_p95_ms}ms objective"
                        },
                    }
                )
        return {
            "apiVersion": "monitoring.coreos.com/v1",
            "kind": "PrometheusRule",
            "metadata": {"name": node.name, "namespace": namespace, "labels": self._labels(plan)},
            "spec": {"groups": [{"name": f"{plan.service}-slo", "rules": rules}]},
        }
