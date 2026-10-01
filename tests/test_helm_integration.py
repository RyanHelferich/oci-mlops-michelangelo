"""Opt-in local Helm integration; needs fetch and pinned renderer dependencies, no cloud."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_installer import installer, sample, ROOT


@unittest.skipUnless(os.environ.get("INSTALLER_RUN_HELM_INTEGRATION") == "1", "Set INSTALLER_RUN_HELM_INTEGRATION=1 after local fetch")
class ActualHelmRender(unittest.TestCase):
    def test_release_chart_router_and_bundled_temporal_sql_tls(self):
        import yaml
        config = sample()
        config["deployment"]["upstream_path"] = str(installer.source_path(config))
        config["platform"]["mysqlRouter"]["python"] = sys.executable
        with tempfile.TemporaryDirectory(dir=ROOT / "deploy") as work:
            root = Path(work)
            shutil.copytree(ROOT / ".generated/chart", root / ".generated/chart")
            renderer = root / "security/mysql-router"
            renderer.mkdir(parents=True)
            (root / "deploy").mkdir()
            shutil.copyfile(ROOT / "deploy/postrenderer.py", root / "deploy/postrenderer.py")
            shutil.copytree(ROOT / "deploy/crds", root / "deploy/crds")
            for name in ("post-renderer.py", "requirements.txt"):
                shutil.copyfile(ROOT / "security/mysql-router" / name, renderer / name)
            env = {}
            vendor = ROOT / "security/mysql-router/.test-deps"
            if vendor.exists():
                env["PYTHONPATH"] = str(vendor)
            with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                installer.render(config, manifests=True, root=root)
            objects = [o for o in yaml.safe_load_all((root / ".generated/manifest.yaml").read_text()) if o]
            self.assertFalse(any(o["kind"] == "Secret" for o in objects))
            deployments = [o for o in objects if o["kind"] == "Deployment"]
            core = [o for o in deployments if o["metadata"]["labels"].get("app.kubernetes.io/component") in ("apiserver", "controllermgr")]
            self.assertEqual(len(core), 2)
            for obj in core:
                init = obj["spec"]["template"]["spec"]["initContainers"]
                self.assertEqual(init[0]["restartPolicy"], "Always")
                self.assertEqual(init[0]["name"], "metadata-mysql-router")
            temporal_config = next(o for o in objects if o["kind"] == "ConfigMap" and o["metadata"]["name"] == "michelangelo-temporal-config")
            # Temporal expands these Go templates inside its container before YAML parsing.
            rendered_config = yaml.safe_load(re.sub(r"\{\{[^{}]+\}\}", "runtime-placeholder", temporal_config["data"]["config_template.yaml"]))
            for name in ("default", "visibility"):
                sql = rendered_config["persistence"]["datastores"][name]["sql"]
                self.assertEqual(sql["pluginName"], "mysql8")
                self.assertTrue(sql["tls"]["enabled"])
                self.assertTrue(sql["tls"]["enableHostVerification"])
                self.assertEqual(sql["tls"]["serverName"], config["platform"]["mysqlRouter"]["host"])
            schema = next(o for o in objects if o["kind"] == "Job" and o["metadata"]["name"] == "michelangelo-temporal-schema")
            init = schema["spec"]["template"]["spec"]["initContainers"]
            self.assertEqual(len(init), 6)
            for container in init:
                variables = {e["name"]: e.get("value") for e in container["env"]}
                self.assertEqual(variables["SQL_TLS"], "true")
                self.assertEqual(variables["SQL_TLS_DISABLE_HOST_VERIFICATION"], "false")
                self.assertEqual(variables["SQL_TLS_CA_FILE"], "/mysql-ca/ca.pem")
                self.assertEqual(container["volumeMounts"][0]["mountPath"], "/mysql-ca")
            self.assertTrue(any(o["kind"] == "Job" and o["metadata"]["name"] == "michelangelo-temporal-namespace-setup" for o in objects))
            receipt = json.loads((root / ".generated/render-receipt.json").read_text())
            self.assertEqual(receipt["manifest_sha256"], installer.file_digest(root / ".generated/manifest.yaml"))


if __name__ == "__main__":
    unittest.main()
