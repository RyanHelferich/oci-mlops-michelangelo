"""Offline contract and deployment safety tests; never contact OCI or a cluster."""
import argparse
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def sample():
    return json.loads((ROOT / "config/config.example.json").read_text(encoding="utf-8"))


def complete():
    config = sample()
    tf = config["terraform"]
    tf.update(tenancy_ocid="ocid1.tenancy.oc1..local", compartment_ocid="ocid1.compartment.oc1..local", node_image_ocid="ocid1.image.oc1.iad.local", availability_domains=["local:US-ASHBURN-AD-1"])
    tf["mysql_certificate_ocid"] = "ocid1.certificate.oc1.iad.local"
    config["platform"]["objectStorage"]["endpoint"] = "local.compat.objectstorage.us-ashburn-1.oraclecloud.com"
    config["platform"]["mysqlRouter"]["host"] = "mysql.db.madev.oraclevcn.com"
    config["deployment"].update(mysql_mvp_approved=True, static_object_storage_credentials_approved=True, enable_mutations=True,
        router_tls_verified=True, kube_context="local-oke", kube_api_server="https://10.42.0.2:6443")
    return config


class ConfigTests(unittest.TestCase):
    def test_example_valid_without_deployment_authorization(self):
        installer.validate(sample())
        with self.assertRaises(installer.InstallerError):
            installer.ready(sample())

    def test_unsupported_backends_fail_closed(self):
        cases = [("metadataStorage", "driver", "postgres"), ("metadataStorage", "driver", "autonomous"),
                 ("objectStorage", "auth", "resource_principal"), ("objectStorage", "secure", False),
                 ("workflow", "engine", "cadence"), ("metadataStorage", "host", "mysql.db.private")]
        for section, key, value in cases:
            with self.subTest(section=section, value=value):
                config = sample()
                config["platform"][section][key] = value
                with self.assertRaises(installer.InstallerError):
                    installer.validate(config)

    def test_type_confusion_unknown_keys_and_oversize_fail(self):
        for key, value in (("node_count", True), ("node_ocpus", "2"), ("node_memory_gbs", 64), ("enable_bastion", "false"), ("region", "eu-frankfurt-1"), ("unrecognized", 1)):
            with self.subTest(key=key):
                config = sample()
                config["terraform"][key] = value
                with self.assertRaises(installer.InstallerError):
                    installer.validate(config)

    def test_inline_secrets_fail_without_echoing_value(self):
        for key in ("mysql_admin_password", "rootPassword", "secretAccessKey", "MYSQL_ADMIN_PASSWORD"):
            config = sample()
            config["terraform"][key] = "never-echo-this-password"
            with self.assertRaises(installer.InstallerError) as error:
                installer.validate(config)
            self.assertNotIn("never-echo", str(error.exception))

    def test_schema_database_is_fixed(self):
        config = sample()
        config["platform"]["metadataStorage"]["database"] = "custom"
        with self.assertRaises(installer.InstallerError):
            installer.validate(config)

    def test_context_namespace_and_bucket_contract(self):
        for section, key, value in (("deployment", "namespace", "wrong"), ("objectStorage", "bucket", "wrong"), ("objectStorage", "endpoint", "https://user:password@host")):
            config = sample()
            target = config[section] if section == "deployment" else config["platform"][section]
            target[key] = value
            with self.assertRaises(installer.InstallerError):
                installer.validate(config)

    def test_production_sizing_keeps_profile_contract(self):
        config = sample()
        config["terraform"].update(environment="prod", node_count=4, node_ocpus=4, node_memory_gbs=32)
        installer.validate(config)
        self.assertEqual(installer.values(config)["temporal"]["server"]["config"]["numHistoryShards"], 512)

    def test_image_digest_and_router_endpoint_validation(self):
        config = sample()
        config["platform"]["images"]["ui"] = "ghcr.io/michelangelo-ai/ui:latest"
        with self.assertRaises(installer.InstallerError):
            installer.validate(config)
        config = sample()
        config["platform"]["mysqlRouter"]["image"] = "mysql-router:8.4.6"
        with self.assertRaises(installer.InstallerError):
            installer.validate(config)

    def test_upstream_shell_interpolation_is_constrained(self):
        for section, key, value in (("workflow", "domain", "default;echo secret"), ("mysqlRouter", "host", "host\nmalicious=1"), ("mysqlRouter", "caKey", "../private.pem")):
            config = sample()
            config["platform"][section][key] = value
            with self.assertRaises(installer.InstallerError):
                installer.validate(config)

    def test_duplicate_json_and_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "bad.json"
            for text in ('{"x":1,"x":2}', '{"x":NaN}'):
                path.write_text(text)
                with self.assertRaises(installer.InstallerError):
                    installer.read_json(path)


class RenderTests(unittest.TestCase):
    def test_values_use_existing_secrets_and_private_services(self):
        values = installer.values(sample())
        self.assertEqual(values["metadataStorage"]["existingSecret"], "michelangelo-mysql")
        self.assertEqual(values["objectStorage"]["existingSecret"], "michelangelo-object-storage")
        self.assertNotIn("rootPassword", values["metadataStorage"])
        self.assertNotIn("accessKeyId", values["objectStorage"])
        self.assertEqual(values["apiserver"]["service"]["type"], "ClusterIP")
        self.assertFalse(values["apiserver"]["ingress"]["enabled"])
        self.assertIn("corsOrigins", values["envoy"])

    def test_temporal_both_stores_require_verified_tls(self):
        values = installer.values(sample())
        temporal = values["temporal"]
        self.assertTrue(temporal["enabled"])
        for store in ("default", "visibility"):
            sql = temporal["server"]["config"]["persistence"][store]["sql"]
            self.assertEqual(sql["driver"], "mysql8")
            self.assertEqual(sql["existingSecret"], "michelangelo-mysql")
            self.assertTrue(sql["tls"]["enabled"])
            self.assertTrue(sql["tls"]["enableHostVerification"])
            self.assertEqual(sql["tls"]["caFile"], "/mysql-ca/ca.pem")
        self.assertTrue(temporal["schema"]["update"]["enabled"])
        self.assertEqual(temporal["server"]["config"]["numHistoryShards"], 16)
        self.assertFalse(temporal["cassandra"]["enabled"])
        self.assertFalse(temporal["prometheus"]["enabled"])

    def test_render_is_deterministic_and_does_not_read_env_secrets(self):
        config = sample()
        with tempfile.TemporaryDirectory() as work, patch.object(installer, "verify_source"), patch.dict(os.environ, {"TF_VAR_mysql_admin_password": "never-render-secret"}):
            root = Path(work)
            with contextlib.redirect_stdout(io.StringIO()):
                installer.render(config, root=root)
            first = (root / ".generated/values.json").read_bytes()
            installer.render(config, root=root)
            self.assertEqual(first, (root / ".generated/values.json").read_bytes())
            for path in (root / ".generated").iterdir():
                self.assertNotIn("never-render-secret", path.read_text())

    def test_managed_secrets_fail_render(self):
        with tempfile.TemporaryDirectory() as work, patch.object(installer, "verify_source"), patch.object(installer, "verify_chart"), patch.object(installer, "helm_renderer_args", return_value=[]), patch.object(installer, "run", return_value="apiVersion: v1\nkind: Secret\ndata:\n  password: leaked\n"):
            with self.assertRaises(installer.InstallerError):
                installer.render(sample(), manifests=True, root=Path(work))


class LifecycleTests(unittest.TestCase):
    def test_install_verifies_review_before_helm_upgrade(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            output = root / ".generated"
            output.mkdir()
            (output / "chart").mkdir()
            (root / "deploy").mkdir()
            (root / "deploy/postrenderer.py").write_text("# wrapper")
            (output / "manifest.yaml").write_text("kind: Deployment\n")
            installer.write_json(output / "values.json", {})
            config = complete()
            digest = installer.file_digest(output / "manifest.yaml")
            receipt = {"manifest_sha256": digest, "config_sha256": installer.fingerprint(config), "chart_sha256": installer.tree_digest(output / "chart"), "renderer_sha256": installer.renderer_digest(root), "values_sha256": installer.file_digest(output / "values.json")}
            installer.write_json(output / "render-receipt.json", receipt)
            args = argparse.Namespace(allow_cluster_mutation=True, confirm_target="local-oke", reviewed_manifest_sha256=digest, sync_secrets=False)
            with patch.object(installer, "verify_source"), patch.object(installer, "verify_chart"), patch.object(installer, "cluster_identity_checks"), patch.object(installer, "cluster_checks"), patch.object(installer, "helm_renderer_args", return_value=[]), patch.object(installer, "smoke"), patch.object(installer, "run", return_value='{"items": []}') as run:
                installer.install(config, args, root=root)
                argv = run.call_args.args[0]
                self.assertEqual(argv[:3], ["helm", "upgrade", "--install"])
                self.assertIn("--atomic", argv)
                self.assertIn("--wait", argv)
                run.reset_mock()
                (output / "values.json").write_text("changed")
                with self.assertRaises(installer.InstallerError):
                    installer.install(config, args, root=root)
                run.assert_not_called()

    def test_successful_apply_uses_only_saved_plan(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / "terraform").mkdir()
            output = root / ".generated"
            output.mkdir()
            plan = output / "deployment.tfplan"
            plan.write_bytes(b"reviewed")
            config = complete()
            digest = installer.file_digest(plan)
            installer.write_json(output / "plan-receipt.json", {"config_sha256": installer.fingerprint(config), "terraform_sha256": installer.tree_digest(root / "terraform"), "plan_sha256": digest})
            args = argparse.Namespace(allow_paid_resources=True, confirm_target=config["terraform"]["compartment_ocid"], reviewed_plan_sha256=digest, ack_sensitive_local_artifacts=True)
            with patch.object(installer, "run") as run, patch.dict(os.environ, {"TF_VAR_mysql_admin_password": "private"}, clear=True):
                installer.terraform_apply(config, args, root=root)
                self.assertEqual(run.call_count, 1)
                argv = run.call_args.args[0]
                self.assertEqual(argv, ["terraform", "apply", "-input=false", "-no-color", plan])
                self.assertNotIn("private", str(argv))

    def test_shutdown_permission_cannot_authorize_paid_deployment(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            output = root / ".generated"
            output.mkdir()
            config = complete()
            installer.write_json(output / "plan-receipt.json", {"operation": "deploy"})
            args = argparse.Namespace(allow_paid_resources=False, allow_shutdown=True,
                confirm_target=config["terraform"]["compartment_ocid"], ack_sensitive_local_artifacts=True)
            with patch.object(installer, "run") as run:
                with self.assertRaisesRegex(installer.InstallerError, "allow-paid-resources"):
                    installer.terraform_apply(config, args, root=root)
                run.assert_not_called()

    def test_shutdown_requires_separate_explicit_permission(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            output = root / ".generated"
            output.mkdir()
            config = complete()
            installer.write_json(output / "plan-receipt.json", {"operation": "shutdown"})
            args = argparse.Namespace(allow_paid_resources=True, allow_shutdown=False,
                confirm_target=config["terraform"]["compartment_ocid"], ack_sensitive_local_artifacts=True)
            with patch.object(installer, "run") as run:
                with self.assertRaisesRegex(installer.InstallerError, "allow-shutdown"):
                    installer.terraform_apply(config, args, root=root)
                run.assert_not_called()

    def test_shutdown_plan_rejects_bucket_deletion_and_creations(self):
        for resource, actions in (("oci_objectstorage_bucket", ["delete"]),
                                  ("oci_core_instance", ["delete", "create"]),
                                  ("oci_identity_policy", ["update"])):
            with self.subTest(resource=resource, actions=actions):
                with self.assertRaises(installer.InstallerError):
                    installer.validate_shutdown_plan({"resource_changes": [
                        {"type": resource, "change": {"actions": actions}}]})

    def test_shutdown_plan_allows_retained_bucket_and_transient_deletion(self):
        installer.validate_shutdown_plan({"resource_changes": [
            {"type": "oci_objectstorage_bucket", "change": {"actions": ["no-op"]}},
            {"type": "oci_containerengine_cluster", "change": {"actions": ["delete"]}}]})

    def test_only_completed_temporal_setup_jobs_are_recreated(self):
        config = complete()
        config["platform"]["workflow"]["mode"] = "bundled"
        name = config["deployment"]["release"] + "-temporal-schema"
        job = {"metadata": {"name": name}, "status": {"conditions": [{"type": "Complete", "status": "True"}]}}
        unrelated = {"metadata": {"name": "customer-training"}, "status": {"conditions": [{"type": "Complete", "status": "True"}]}}
        with patch.object(installer, "run", side_effect=[json.dumps({"items": [job, unrelated]}), ""]) as run:
            installer.remove_completed_setup_jobs(config)
            self.assertEqual(run.call_count, 2)
            self.assertIn(name, run.call_args.args[0])
            self.assertNotIn("customer-training", run.call_args.args[0])
        job["status"] = {"active": 1}
        with patch.object(installer, "run", return_value=json.dumps({"items": [job]})) as run:
            with self.assertRaises(installer.InstallerError):
                installer.remove_completed_setup_jobs(config)
            self.assertEqual(run.call_count, 1)

    def test_secret_sync_uses_stdin_without_secrets_in_argv(self):
        config = complete()
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            ca, cert, key = root / "ca.pem", root / "cert.pem", root / "key.pem"
            ca.write_text("-----BEGIN CERTIFICATE-----\npublic-ca\n")
            cert.write_text("-----BEGIN CERTIFICATE-----\nrouter-cert\n")
            key.write_text("-----BEGIN PRIVATE KEY-----\nrouter-only-key\n")
            env = {"MYSQL_PASSWORD": "private-password", "AWS_ACCESS_KEY_ID": "private-id", "AWS_SECRET_ACCESS_KEY": "private-key", "MYSQL_CA_FILE": str(ca), "MYSQL_ROUTER_AUTH_CERT_FILE": str(cert), "MYSQL_ROUTER_AUTH_KEY_FILE": str(key)}
            with patch.dict(os.environ, env, clear=True), patch.object(installer, "run") as run:
                installer.sync_secrets(config)
                self.assertNotIn("private", str(run.call_args.args[0]))
                data = json.loads(run.call_args.kwargs["input_text"])
                self.assertEqual(len(data["items"]), 4)
                self.assertEqual(data["items"][-1]["type"], "kubernetes.io/tls")
                self.assertIn("--server-side", run.call_args.args[0])

    def test_ca_secret_never_accepts_server_private_key(self):
        config = complete()
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            ca = root / "ca.pem"
            ca.write_text("-----BEGIN CERTIFICATE-----\n-----BEGIN PRIVATE KEY-----\n")
            env = {"MYSQL_PASSWORD": "private-password", "AWS_ACCESS_KEY_ID": "private-id", "AWS_SECRET_ACCESS_KEY": "private-key", "MYSQL_CA_FILE": str(ca), "MYSQL_ROUTER_AUTH_CERT_FILE": str(ca), "MYSQL_ROUTER_AUTH_KEY_FILE": str(ca)}
            with patch.dict(os.environ, env, clear=True), patch.object(installer, "run") as run:
                with self.assertRaises(installer.InstallerError):
                    installer.sync_secrets(config)
                run.assert_not_called()

    def test_apply_requires_local_and_cli_authorization(self):
        for enabled, allowed, confirmation in ((False, True, "target"), (True, False, "target"), (True, True, "other")):
            config = complete()
            config["deployment"]["enable_mutations"] = enabled
            with self.assertRaises(installer.InstallerError):
                installer.mutation_gate(config, allowed, confirmation, "target")

    def test_environment_override_rejected(self):
        for key in ("TF_VAR_compartment_ocid", "TF_CLI_ARGS", "TF_CLI_ARGS_apply"):
            with patch.dict(os.environ, {key: "dangerous", "TF_VAR_mysql_admin_password": "private"}, clear=True):
                with self.assertRaises(installer.InstallerError):
                    installer.terraform_env(complete())

    def test_mysql_secret_required_only_in_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(installer.InstallerError):
                installer.terraform_env(complete())
        with patch.dict(os.environ, {"TF_VAR_mysql_admin_password": "private"}, clear=True):
            self.assertEqual(installer.terraform_env(complete())["TF_IN_AUTOMATION"], "1")

    def test_plan_missing_read_authorization_never_runs(self):
        args = argparse.Namespace(allow_cloud_read=False, ack_sensitive_local_artifacts=True)
        with patch.object(installer, "run") as run:
            with self.assertRaises(installer.InstallerError):
                installer.terraform_plan(complete(), args)
            run.assert_not_called()

    def test_apply_refuses_changed_plan_or_code(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / "terraform").mkdir()
            (root / "terraform/main.tf").write_text("# test\n")
            output = root / ".generated"
            output.mkdir()
            plan = output / "deployment.tfplan"
            plan.write_bytes(b"fake-reviewed-plan")
            config = complete()
            digest = installer.file_digest(plan)
            receipt = {"config_sha256": installer.fingerprint(config), "terraform_sha256": installer.tree_digest(root / "terraform"), "plan_sha256": digest}
            installer.write_json(output / "plan-receipt.json", receipt)
            args = argparse.Namespace(allow_paid_resources=True, confirm_target=config["terraform"]["compartment_ocid"], reviewed_plan_sha256=digest, ack_sensitive_local_artifacts=True)
            for changed in ("plan", "code", "config"):
                with self.subTest(changed=changed), patch.object(installer, "run") as run:
                    if changed == "plan":
                        plan.write_bytes(b"changed")
                    elif changed == "code":
                        plan.write_bytes(b"fake-reviewed-plan")
                        (root / "terraform/main.tf").write_text("# changed\n")
                    else:
                        (root / "terraform/main.tf").write_text("# test\n")
                        config["terraform"]["node_count"] = 2
                    with self.assertRaises(installer.InstallerError):
                        installer.terraform_apply(config, args, root=root)
                    run.assert_not_called()

    def test_install_refuses_unverified_router_before_tools(self):
        config = complete()
        config["deployment"]["router_tls_verified"] = False
        with patch.object(installer, "run") as run:
            with self.assertRaises(installer.InstallerError):
                installer.install(config, argparse.Namespace())
            run.assert_not_called()

    def test_install_refuses_disabled_mutations_before_tools(self):
        config = complete()
        config["deployment"]["enable_mutations"] = False
        with patch.object(installer, "run") as run:
            with self.assertRaises(installer.InstallerError):
                installer.install(config, argparse.Namespace(allow_cluster_mutation=True, confirm_target="local-oke"))
            run.assert_not_called()

    def test_external_workflow_requires_namespace_attestation(self):
        config = complete()
        config["platform"]["workflow"]["mode"] = "external"
        config["deployment"]["workflow_namespace_registered"] = False
        with self.assertRaises(installer.InstallerError):
            installer.ready(config, platform=True)

    def test_cluster_context_mismatch_and_old_version_fail(self):
        config = complete()
        for server, version in (("https://wrong:6443", "34"), (config["deployment"]["kube_api_server"], "28")):
            outputs = [json.dumps({"clusters": [{"cluster": {"server": server}}]}), json.dumps({"serverVersion": {"major": "1", "minor": version}})]
            with patch.object(installer, "run", side_effect=outputs):
                with self.assertRaises(installer.InstallerError):
                    installer.cluster_checks(config)

    def test_missing_secret_keys_fail_without_value_output(self):
        config = complete()
        outputs = [json.dumps({"clusters": [{"cluster": {"server": config["deployment"]["kube_api_server"]}}]}), json.dumps({"serverVersion": {"major": "1", "minor": "34+"}}), "namespace/michelangelo", json.dumps({"data": {"wrong": "never-display"}})]
        with patch.object(installer, "run", side_effect=outputs):
            with self.assertRaises(installer.InstallerError) as error:
                installer.cluster_checks(config)
            self.assertNotIn("never-display", str(error.exception))

    def test_subprocess_errors_never_echo_diagnostics(self):
        result = subprocess.CompletedProcess(["git"], 1, stdout="secret-value", stderr="password=secret-value")
        with patch.object(installer.shutil, "which", return_value="git"), patch.object(installer.subprocess, "run", return_value=result):
            with self.assertRaises(installer.InstallerError) as error:
                installer.run(["git", "status"])
            self.assertNotIn("secret-value", str(error.exception))


class SourcePinTests(unittest.TestCase):
    def test_wrong_head_rejected(self):
        with tempfile.TemporaryDirectory() as work, patch.object(installer, "run", return_value="wrong-head\n"):
            with self.assertRaises(installer.InstallerError):
                installer.verify_source(Path(work))

    def test_changed_working_file_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / "file.txt").write_text("changed")
            outputs = [installer.REVISION + "\n", "100644 blob expected\tfile.txt\n", "different\n", "different\n"]
            with patch.object(installer, "run", side_effect=outputs):
                with self.assertRaises(installer.InstallerError):
                    installer.verify_source(root)

    def test_exact_raw_blob_accepted_when_clean_filter_changes_newlines(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            path = root / "helm/michelangelo/Chart.yaml"
            path.parent.mkdir(parents=True)
            path.write_text("version: 0.11.0\n")
            outputs = [installer.REVISION + "\n", "100644 blob expected\thelm/michelangelo/Chart.yaml\n", "clean-conversion\n", "expected\n"]
            with patch.object(installer, "run", side_effect=outputs):
                self.assertEqual(installer.verify_source(root)["tracked_files"], 1)

    def test_missing_tracked_file_rejected(self):
        with tempfile.TemporaryDirectory() as work, patch.object(installer, "run", side_effect=[installer.REVISION, "100644 blob expected\tfile.txt\n"]):
            with self.assertRaises(installer.InstallerError):
                installer.verify_source(Path(work))


if __name__ == "__main__":
    unittest.main()
