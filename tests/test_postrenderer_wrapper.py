import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wrapper", ROOT / "deploy/postrenderer.py")
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)

JOB = """---
# Source: michelangelo/templates/core/temporal-namespace-setup-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: michelangelo-temporal-namespace-setup
  labels:
    app.kubernetes.io/name: michelangelo
    app.kubernetes.io/component: temporal-namespace-setup
    app.kubernetes.io/name: temporal
    app.kubernetes.io/component: schema
spec:
  backoffLimit: 10
"""


class NamespaceNormalizationTests(unittest.TestCase):
    def test_known_upstream_metadata_duplicate_pair_normalized(self):
        text = wrapper.normalize_namespace_labels(JOB)
        self.assertEqual(text.count("app.kubernetes.io/name:"), 1)
        self.assertEqual(text.count("app.kubernetes.io/component:"), 1)
        self.assertIn("app.kubernetes.io/name: temporal", text)
        self.assertEqual(wrapper.normalize_namespace_labels(text), text)

    def test_unexpected_duplicate_or_job_shape_rejected(self):
        for text in (JOB.replace("name: temporal\n", "name: unknown\n"), JOB.replace("kind: Job", "kind: Deployment"), JOB.replace("michelangelo-temporal-namespace-setup", "other-job")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                wrapper.normalize_namespace_labels(text)

    def test_other_document_duplicates_are_preserved_for_strict_renderer(self):
        text = JOB.replace(wrapper.SOURCE, "# Source: other/template.yaml")
        self.assertEqual(wrapper.normalize_namespace_labels(text), text)


if __name__ == "__main__":
    unittest.main()
