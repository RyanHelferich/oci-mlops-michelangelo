"""Regressions for failures observed in the first real OCI deployment."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('live_wrapper', ROOT / 'deploy/postrenderer.py')
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


class OCICompatibilityTests(unittest.TestCase):
    def test_crio_support_images_are_qualified_and_pinned(self):
        pod = {'kind': 'Pod', 'spec': {'containers': [{'image': 'temporalio/server:1.24.2'}],
            'initContainers': [{'image': 'mysql:8.0'}]}}
        wrapper.qualify_images([pod])
        for container in pod['spec']['containers'] + pod['spec']['initContainers']:
            self.assertTrue(container['image'].startswith('docker.io/'))
            self.assertIn('@sha256:', container['image'])

    def test_runtime_cannot_mutate_crds_or_delete_namespaces(self):
        role = {'kind': 'ClusterRole', 'rules': [
            {'apiGroups': ['apiextensions.k8s.io'], 'resources': ['customresourcedefinitions'], 'verbs': ['create', 'update']},
            {'apiGroups': [''], 'resources': ['namespaces'], 'verbs': ['get', 'list', 'create', 'delete']}]}
        documents = wrapper.install_crds([role])
        self.assertEqual(sum(d.get('kind') == 'CustomResourceDefinition' for d in documents), 11)
        self.assertFalse(any('customresourcedefinitions' in rule['resources'] for rule in role['rules']))
        self.assertNotIn('delete', role['rules'][0]['verbs'])

    def test_ui_uses_unprivileged_port_and_temporary_filesystem(self):
        deployment = {'kind': 'Deployment', 'metadata': {'name': 'ma-ui', 'namespace': 'ma',
            'labels': {'app.kubernetes.io/component': 'ui'}}, 'spec': {'template': {'spec': {
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 65534},
                'containers': [{'name': 'ui', 'ports': [{'name': 'http', 'containerPort': 80}],
                    'securityContext': {'readOnlyRootFilesystem': False}}]}}}}
        result = wrapper.secure_ui([copy.deepcopy(deployment)])
        pod = result[0]['spec']['template']['spec']
        self.assertEqual(pod['containers'][0]['ports'][0]['containerPort'], 8080)
        self.assertTrue(pod['containers'][0]['securityContext']['readOnlyRootFilesystem'])
        self.assertTrue(pod['securityContext']['runAsNonRoot'])
        self.assertIn('try_files $uri $uri/ /index.html', result[1]['data']['nginx.conf'])


if __name__ == '__main__':
    unittest.main()
