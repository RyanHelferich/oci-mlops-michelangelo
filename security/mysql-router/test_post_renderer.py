"""Transform contract tests, plus an optional real pinned-chart render."""
import copy
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import yaml

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("renderer", HERE / "post-renderer.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
IMAGE = "container-registry.oracle.com/mysql/community-router:8.4.6@sha256:468ea2e12477d6c1d8256f575aac33f09a3281531ef982f13bfeea04b1ba02de"
ENV = {"MYSQL_ROUTER_IMAGE": IMAGE, "MYSQL_ROUTER_HOST": "mysql.example.internal", "MYSQL_ROUTER_CA_SECRET": "mysql-ca", "MYSQL_ROUTER_AUTH_SECRET": "mysql-router-auth"}


def fixture(component="apiserver", namespace="controlplane"):
    name = "release-" + component
    base = {component: {}, "mysql": {"host": "127.0.0.1", "port": 6446}}
    pod = {"containers": [{"name": renderer.COMPONENTS[component], "image": "example:test", "volumeMounts": [{"name": "config", "mountPath": "/config"}]}],
           "volumes": [{"name": "config", "configMap": {"name": name + "-config"}}]}
    if component == "apiserver":
        pod["initContainers"] = [{"name": name, "command": ["/bin/sh", "-c", "mysql --host=$METADATA_HOST"],
                                  "env": [{"name": "METADATA_HOST", "value": "127.0.0.1"}, {"name": "METADATA_PORT", "value": "6446"}]}
                                 for name in ("wait-for-metadata-storage", "schema-init")]
    return [{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": namespace,
             "labels": {"app.kubernetes.io/part-of": "michelangelo", "app.kubernetes.io/component": component}},
             "spec": {"template": {"metadata": {}, "spec": pod}}},
            {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name + "-config", "namespace": namespace},
             "data": {"base.yaml": yaml.safe_dump(base)}}]


def transform(docs, env=ENV):
    return list(yaml.safe_load_all(renderer.transform(yaml.safe_dump_all(docs), env)))


class TransportTests(unittest.TestCase):
    def test_api_native_init_precedes_database_init_and_security(self):
        docs = transform(fixture())
        pod = docs[0]["spec"]["template"]["spec"]
        self.assertEqual([c["name"] for c in pod["initContainers"]], [renderer.ROUTER, "wait-for-metadata-storage", "schema-init"])
        router = pod["initContainers"][0]
        self.assertEqual(router["restartPolicy"], "Always")
        self.assertEqual(router["command"], ["mysqlrouter"])
        self.assertEqual(router["args"], ["--config", "/router/config/mysqlrouter.conf"])
        self.assertEqual(router["securityContext"]["capabilities"], {"drop": ["ALL"]})
        self.assertTrue(router["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(router["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
        self.assertGreater(router["securityContext"]["runAsUser"], 0)
        self.assertIn("requests", router["resources"])
        self.assertNotIn("tcpSocket", router["startupProbe"])
        self.assertEqual(len([v for v in pod["volumes"] if "emptyDir" in v]), 2)
        conf = docs[-1]["data"]["mysqlrouter.conf"]
        for setting in ("bind_address = 127.0.0.1", "bind_port = 6446", "server_ssl_mode = REQUIRED", "server_ssl_verify = VERIFY_IDENTITY", "client_ssl_mode = PREFERRED", "server_ssl_ca = /router/ca/ca.pem", "client_ssl_key = /router/auth/tls.key"):
            self.assertIn(setting, conf)
        self.assertNotIn("metadata_cache", conf)

    def test_controller_injection_and_temporal_untouched(self):
        temporal = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "temporal", "labels": {"app.kubernetes.io/component": "frontend"}}, "spec": {"template": {"spec": {"containers": [{"name": "temporal", "env": [{"name": "SQL_TLS_ENABLED", "value": "true"}]}]}}}}
        source = fixture() + fixture("controllermgr") + [temporal]
        result = transform(source)
        self.assertEqual(result[4], temporal)
        self.assertEqual(result[2]["spec"]["template"]["spec"]["initContainers"][0]["name"], renderer.ROUTER)

    def test_group_access_to_distinct_rsa_secret_and_state(self):
        docs = fixture()
        docs[0]["spec"]["template"]["spec"]["securityContext"] = {"fsGroup": 10001}
        result = transform(docs)
        pod = result[0]["spec"]["template"]["spec"]
        self.assertEqual(pod["securityContext"]["fsGroup"], 10001)
        self.assertEqual(pod["initContainers"][0]["securityContext"]["runAsGroup"], 10001)
        auth = next(v["secret"] for v in pod["volumes"] if v["name"] == "mysql-router-auth")
        self.assertEqual(auth["secretName"], "mysql-router-auth")
        self.assertEqual(auth["defaultMode"], 0o440)
        self.assertEqual({item["key"] for item in auth["items"]}, {"tls.crt", "tls.key"})

    def test_idempotent_no_duplicate_router_or_configmap(self):
        once = transform(fixture() + fixture("controllermgr"))
        self.assertEqual(transform(once), once)

    def test_malformed_and_duplicate_yaml_rejected(self):
        for text in ("[invalid", "kind: Deployment\nkind: Secret", "- list", "", "apiVersion: v1\nkind: List\nitems: []"):
            with self.subTest(text=text), self.assertRaises(renderer.TransportError):
                renderer.transform(text, ENV)

    def test_remote_plaintext_configuration_and_init_rejected(self):
        for change in ("config-host", "config-port", "init-host", "init-port", "postgres"):
            docs = fixture()
            if change.startswith("config"):
                base = yaml.safe_load(docs[1]["data"]["base.yaml"])
                base["mysql"]["host" if change.endswith("host") else "port"] = "remote.example.internal" if change.endswith("host") else 3306
                docs[1]["data"]["base.yaml"] = yaml.safe_dump(base)
            elif change == "postgres":
                docs[0]["spec"]["template"]["spec"]["initContainers"][0]["command"] = ["psql"]
            else:
                init = docs[0]["spec"]["template"]["spec"]["initContainers"][0]
                init["env"][0 if change.endswith("host") else 1]["value"] = "bad"
            with self.subTest(change=change), self.assertRaises(renderer.TransportError):
                transform(docs)

    def test_image_ca_destination_config_injection_rejected(self):
        for key, value in (("MYSQL_ROUTER_IMAGE", "community-router:latest"), ("MYSQL_ROUTER_CA_SECRET", ""), ("MYSQL_ROUTER_AUTH_SECRET", ""), ("MYSQL_ROUTER_AUTH_SECRET", "mysql-ca"), ("MYSQL_ROUTER_HOST", "127.0.0.1"), ("MYSQL_ROUTER_HOST", "localhost"), ("MYSQL_ROUTER_HOST", "mysql.example\nserver_ssl_mode=DISABLED"), ("MYSQL_ROUTER_CA_KEY", "../ca.pem"), ("MYSQL_ROUTER_PORT", "0")):
            with self.subTest(key=key, value=value), self.assertRaises(renderer.TransportError):
                transform(fixture(), dict(ENV, **{key: value}))

    def test_conflicting_duplicate_router_routes_rejected(self):
        for mutation in ("router", "config", "volume", "position", "hostnetwork", "duplicate"):
            docs = transform(fixture())
            pod = docs[0]["spec"]["template"]["spec"]
            if mutation == "router":
                pod["initContainers"][0]["image"] = "insecure:latest"
            elif mutation == "config":
                docs[-1]["data"]["mysqlrouter.conf"] += "\n[routing:plaintext]\nserver_ssl_mode=DISABLED\n"
            elif mutation == "volume":
                pod["volumes"][-2]["emptyDir"] = {}
            elif mutation == "position":
                pod["initContainers"].append(pod["initContainers"].pop(0))
            elif mutation == "hostnetwork":
                pod["hostNetwork"] = True
            else:
                pod["initContainers"].append(copy.deepcopy(pod["initContainers"][0]))
            with self.subTest(mutation=mutation), self.assertRaises(renderer.TransportError):
                transform(docs)

    def test_cli_failure_has_no_partial_manifest_or_secret_echo(self):
        proc = subprocess.run([os.sys.executable, str(HERE / "post-renderer.py")], input="kind: [\nsecret: DO_NOT_ECHO", text=True, capture_output=True, env=dict(os.environ, **ENV))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("DO_NOT_ECHO", proc.stderr)

    def test_correct_release_namespace_selection_and_long_names(self):
        docs = fixture(namespace="another")
        docs[0]["metadata"]["name"] = "x" * 63
        output = transform(docs)
        self.assertLessEqual(len(output[-1]["metadata"]["name"]), 63)
        self.assertEqual(output[-1]["metadata"]["namespace"], "another")

    def test_missing_or_wrong_component_config_rejected(self):
        for change in ("missing", "wrong", "disabled-label"):
            docs = fixture()
            if change == "missing":
                docs.pop()
            elif change == "wrong":
                docs[1]["data"]["base.yaml"] = "mysql: {host: '127.0.0.1', port: 6446}\ncontrollermgr: {}"
            else:
                docs[0]["metadata"]["labels"]["app.kubernetes.io/part-of"] = "temporal"
            with self.subTest(change=change), self.assertRaises(renderer.TransportError):
                transform(docs)

    @unittest.skipUnless(os.environ.get("MYSQL_ROUTER_TEST_HELM") and os.environ.get("MYSQL_ROUTER_TEST_UPSTREAM"), "set MYSQL_ROUTER_TEST_HELM and MYSQL_ROUTER_TEST_UPSTREAM to render the pinned chart")
    def test_actual_upstream_chart(self):
        upstream = Path(os.environ["MYSQL_ROUTER_TEST_UPSTREAM"])
        self.render_chart(upstream, "d717c2b1f8d2512cb6859d5560d54960471dc89f")

    @unittest.skipUnless(os.environ.get("MYSQL_ROUTER_TEST_HELM") and os.environ.get("MYSQL_ROUTER_TEST_UPSTREAM"), "set chart test environment variables")
    def test_actual_released_chart(self):
        self.render_chart(Path(os.environ["MYSQL_ROUTER_TEST_UPSTREAM"]), "37852aace4dd9f9658c11c19c56d19692ac5d2c4")

    def render_chart(self, upstream, revision):
        archive = subprocess.check_output(["git", "-C", str(upstream), "archive", "--format=zip", revision, "helm/michelangelo"])
        with tempfile.TemporaryDirectory() as tmp:
            chart = Path(tmp) / "chart"
            with zipfile.ZipFile(io.BytesIO(archive)) as files:
                for entry in files.infolist():
                    if entry.is_dir():
                        continue
                    relative = Path(entry.filename).relative_to("helm/michelangelo")
                    target = chart / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(files.read(entry))
            # Core templates have no dependency references. Strip dependency
            # declarations in this disposable test copy to render offline.
            chart_meta = yaml.safe_load((chart / "Chart.yaml").read_text())
            chart_meta.pop("dependencies", None)
            (chart / "Chart.yaml").write_text(yaml.safe_dump(chart_meta))
            command = [os.environ["MYSQL_ROUTER_TEST_HELM"], "template", "transport", str(chart), "--namespace", "controlplane", "--set", "metadataStorage.host=127.0.0.1", "--set", "metadataStorage.port=6446", "--set", "metadataStorage.existingSecret=mysql-password", "--set", "objectStorage.endpoint=s3.example.internal", "--set", "objectStorage.existingSecret=s3-credentials", "--set", "workflow.engine=temporal", "--set", "workflow.endpoint=temporal:7233", "--set", "cadence.enabled=false", "--set", "temporal.enabled=false", "--set", "ui.enabled=false"]
            rendered = subprocess.check_output(command, text=True, encoding="utf-8")
            result = list(yaml.safe_load_all(renderer.transform(rendered, ENV)))
            targets = [d for d in result if d["kind"] == "Deployment" and d["metadata"].get("labels", {}).get("app.kubernetes.io/component") in renderer.COMPONENTS]
            self.assertEqual(len(targets), 2)
            for target in targets:
                self.assertEqual(target["spec"]["template"]["spec"]["initContainers"][0]["restartPolicy"], "Always")
            self.assertEqual(transform(result), result)
            # Exercise Helm's real stdin/stdout post-renderer invocation with
            # the cross-platform interpreter+script contract used by installer.
            helm_output = subprocess.check_output(command + ["--post-renderer", os.sys.executable, "--post-renderer-args", str(HERE / "post-renderer.py")],
                                                  text=True, encoding="utf-8", env=dict(os.environ, **ENV))
            self.assertEqual({renderer.resource_key(d): d for d in yaml.safe_load_all(helm_output)},
                             {renderer.resource_key(d): d for d in result})


if __name__ == "__main__":
    unittest.main(verbosity=2)
