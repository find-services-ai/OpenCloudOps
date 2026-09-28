"""Every IaC backend, checked for the properties that matter downstream."""

from __future__ import annotations

import json

import pytest
import yaml

from opencloudops.emitters import available, get_emitter
from opencloudops.emitters.base import EmitterError
from opencloudops.learner import Learner
from opencloudops.spec import NodeType

BACKENDS = available()


@pytest.mark.parametrize("backend", BACKENDS)
class TestEveryBackend:
    def test_emits_files(self, backend, prod_plan, platform):
        files = get_emitter(backend).emit(prod_plan, platform)
        assert files, f"{backend} emitted nothing"
        assert all(content.strip() for content in files.values())

    def test_is_deterministic(self, backend, prod_plan, platform):
        """Non-determinism produces a spurious diff on every run, which makes
        review worthless -- so it is a test, not a preference."""
        first = get_emitter(backend).emit(prod_plan, platform)
        second = get_emitter(backend).emit(prod_plan, platform)
        assert first == second

    def test_carries_the_resolved_names(self, backend, prod_plan, platform):
        """Emitters must not recompute names. Every rendered resource name has
        to be one the core produced."""
        blob = "\n".join(get_emitter(backend).emit(prod_plan, platform).values())
        emitter = get_emitter(backend)
        for node in emitter.supported_nodes(prod_plan):
            assert node.name in blob, f"{backend} did not render the resolved name for {node.id}"

    def test_declares_what_it_skipped(self, backend, prod_plan, platform):
        emitter = get_emitter(backend)
        files = emitter.emit(prod_plan, platform)
        if emitter.skipped_nodes(prod_plan):
            assert "UNSUPPORTED.md" in files
            for node in emitter.skipped_nodes(prod_plan):
                assert node.id in files["UNSUPPORTED.md"]

    def test_passes_its_own_lessons(self, backend, prod_plan, platform):
        """The generator and the checks must agree. If the framework's own
        output trips its own detectors, one of the two is wrong."""
        files = get_emitter(backend).emit(prod_plan, platform)
        hits = Learner(memory_dir=None).scan(files, prod_plan, platform)
        blocking = [h for h in hits if h.severity.value == "HIGH"]
        assert not blocking, [f"{h.lesson_id} {h.artifact}:{h.line} {h.excerpt}" for h in blocking]

    def test_no_other_environment_leaks_in(self, backend, dev_plan, platform):
        """A dev artifact naming prod is the contamination that survives review."""
        blob = "\n".join(get_emitter(backend).emit(dev_plan, platform).values())
        for env in platform.environments:
            if env.name == dev_plan.environment:
                continue
            assert f"-{env.short}-" not in blob, f"{backend} leaked {env.name} into {dev_plan.environment}"


def test_unknown_backend_is_refused():
    with pytest.raises(EmitterError, match="no IaC backend"):
        get_emitter("ansible")


class TestTerraform:
    def test_no_wildcard_iam(self, prod_plan, platform):
        blob = "\n".join(get_emitter("terraform").emit(prod_plan, platform).values())
        assert '"Action" = "*"' not in blob
        assert '"Resource" = "*"' not in blob

    def test_no_open_egress(self, prod_plan, platform):
        blob = "\n".join(get_emitter("terraform").emit(prod_plan, platform).values())
        assert "0.0.0.0/0" not in blob

    def test_secret_value_is_ignored_after_creation(self, prod_plan, platform):
        blob = "\n".join(get_emitter("terraform").emit(prod_plan, platform).values())
        assert "ignore_changes = [secret_string]" in blob

    def test_environment_lives_in_one_variable(self, prod_plan, platform):
        """Two environment variables is how cross-environment leaks stop being
        detectable -- the single most expensive lesson in the catalog."""
        variables = get_emitter("terraform").emit(prod_plan, platform)["variables.tf"]
        assert variables.count('variable "environment"') == 1
        assert 'variable "env_label"' not in variables

    def test_production_database_is_protected(self, prod_plan, platform):
        main = get_emitter("terraform").emit(prod_plan, platform)["main.tf"]
        assert "deletion_protection     = true" in main
        assert "skip_final_snapshot     = false" in main

    def test_tfvars_is_written_per_environment(self, dev_plan, platform):
        files = get_emitter("terraform").emit(dev_plan, platform)
        assert "tfvars/dev.tfvars" in files


class TestCloudFormation:
    def test_template_is_valid_yaml_with_the_expected_shape(self, prod_plan, platform):
        files = get_emitter("cloudformation").emit(prod_plan, platform)
        template = yaml.safe_load(files["template.yaml"])
        assert template["AWSTemplateFormatVersion"] == "2010-09-09"
        assert template["Resources"]
        for name, resource in template["Resources"].items():
            assert "Type" in resource, f"{name} has no Type"

    def test_parameters_file_is_valid_json(self, prod_plan, platform):
        files = get_emitter("cloudformation").emit(prod_plan, platform)
        params = json.loads(files["parameters/prod.json"])
        assert {p["ParameterKey"] for p in params} >= {"EnvironmentName"}

    def test_every_ref_points_at_something_real(self, prod_plan, platform):
        template = yaml.safe_load(
            get_emitter("cloudformation").emit(prod_plan, platform)["template.yaml"]
        )
        known = set(template["Resources"]) | set(template["Parameters"]) | {
            "AWS::Region", "AWS::AccountId", "AWS::Partition", "AWS::StackName", "AWS::NoValue",
        }

        def walk(node):
            if isinstance(node, dict):
                if set(node) == {"Ref"} and isinstance(node["Ref"], str):
                    assert node["Ref"] in known, f"dangling Ref to {node['Ref']}"
                if "Fn::GetAtt" in node:
                    target = node["Fn::GetAtt"][0]
                    assert target in template["Resources"], f"dangling GetAtt to {target}"
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(template["Resources"])

    def test_production_database_retains_on_delete(self, prod_plan, platform):
        template = yaml.safe_load(
            get_emitter("cloudformation").emit(prod_plan, platform)["template.yaml"]
        )
        db = next(r for r in template["Resources"].values() if r["Type"] == "AWS::RDS::DBInstance")
        assert db["DeletionPolicy"] == "Snapshot"


class TestPulumi:
    def test_program_is_valid_python(self, prod_plan, platform):
        program = get_emitter("pulumi-python").emit(prod_plan, platform)["__main__.py"]
        compile(program, "__main__.py", "exec")

    def test_stack_config_is_valid_yaml(self, prod_plan, platform):
        files = get_emitter("pulumi-python").emit(prod_plan, platform)
        config = yaml.safe_load(files["Pulumi.prod.yaml"])
        assert config["config"]["aws:region"] == prod_plan.region


class TestCdk:
    def test_stack_declares_the_expected_constructs(self, prod_plan, platform):
        files = get_emitter("cdk-typescript").emit(prod_plan, platform)
        stack = files[f"lib/{prod_plan.graph_name}-stack.ts"]
        assert "export class" in stack and "extends Stack" in stack
        assert stack.count("{") == stack.count("}"), "unbalanced braces in generated TypeScript"

    def test_package_and_tsconfig_are_valid_json(self, prod_plan, platform):
        files = get_emitter("cdk-typescript").emit(prod_plan, platform)
        assert json.loads(files["package.json"])["name"] == prod_plan.graph_name
        assert json.loads(files["tsconfig.json"])["compilerOptions"]["strict"] is True
        assert json.loads(files["cdk.json"])["context"]["environment"] == prod_plan.environment

    def test_buckets_block_public_access_explicitly(self, prod_plan, platform):
        stack = get_emitter("cdk-typescript").emit(prod_plan, platform)[
            f"lib/{prod_plan.graph_name}-stack.ts"
        ]
        assert "BlockPublicAccess.BLOCK_ALL" in stack
        assert "enforceSSL: true" in stack


class TestKubernetes:
    def test_manifests_are_valid_yaml_documents(self, prod_plan, platform):
        files = get_emitter("kubernetes").emit(prod_plan, platform)
        for path, content in files.items():
            if not path.endswith((".yaml", ".yml")):
                continue
            docs = [d for d in yaml.safe_load_all(content) if d]
            assert docs, f"{path} has no documents"
            for doc in docs:
                assert "apiVersion" in doc and "kind" in doc

    def test_pods_run_non_root_with_a_read_only_root_filesystem(self, prod_plan, platform):
        files = get_emitter("kubernetes").emit(prod_plan, platform)
        deployment = next(
            d
            for d in yaml.safe_load_all(files["api/deployment.yaml"])
            if d and d["kind"] == "Deployment"
        )
        pod = deployment["spec"]["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True
        assert pod["containers"][0]["securityContext"]["capabilities"]["drop"] == ["ALL"]

    def test_namespace_enforces_restricted_pod_security(self, prod_plan, platform):
        files = get_emitter("kubernetes").emit(prod_plan, platform)
        ns = next(d for d in yaml.safe_load_all(files["namespace.yaml"]) if d)
        assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"

    def test_secrets_are_referenced_not_embedded(self, prod_plan, platform):
        """A Kubernetes Secret is base64, not encryption. Emitting one would put
        the value in git."""
        files = get_emitter("kubernetes").emit(prod_plan, platform)
        blob = "\n".join(files.values())
        assert "kind: ExternalSecret" in blob
        assert "kind: Secret\n" not in blob

    def test_network_policy_is_default_deny(self, prod_plan, platform):
        files = get_emitter("kubernetes").emit(prod_plan, platform)
        policy = next(
            d
            for d in yaml.safe_load_all(files["api/deployment.yaml"])
            if d and d["kind"] == "NetworkPolicy"
        )
        assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}

    def test_it_refuses_to_pretend_it_covered_the_cloud_resources(self, prod_plan, platform):
        emitter = get_emitter("kubernetes")
        skipped = {n.type for n in emitter.skipped_nodes(prod_plan)}
        assert NodeType.relational in skipped and NodeType.object_store in skipped
