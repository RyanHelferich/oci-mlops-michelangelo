"""Run the OCI SDK workload identity probe in OKE without static credentials."""
import json
import argparse
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'docker.io/library/python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect-denied', action='store_true', help='Verify the unapproved default service account cannot access artifacts')
    args = parser.parse_args()
    if args.expect_denied:
        # Prove the configured bucket exists and the approved account can access
        # it before interpreting OCI's resource-hiding 404 as an IAM denial.
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())])
        if result.returncode:
            raise RuntimeError('Approved identity baseline failed; denial result would be inconclusive')
    config = json.loads((ROOT / 'config/config.json').read_text(encoding='utf-8-sig'))
    outputs = json.loads((ROOT / '.local/terraform-outputs.json').read_text(encoding='utf-8-sig'))
    deployment = config['deployment']
    if not deployment['enable_mutations'] or not config['terraform']['artifact_allow_delete']:
        raise RuntimeError('Requires enabled mutations and scoped probe cleanup permission')
    kube = ['kubectl', '--context', deployment['kube_context'], '-n', deployment['namespace']]

    def run(arguments, payload=None, optional=False):
        result = subprocess.run(kube + arguments, input=payload, capture_output=True, text=True)
        if result.returncode and not optional:
            raise RuntimeError('Native identity diagnostic failed; inspect local Job evidence')
        return result.stdout

    cluster = json.loads(run(['config', 'view', '--minify', '-o', 'json']))['clusters'][0]['cluster']
    if cluster['server'] != deployment['kube_api_server'] or cluster.get('insecure-skip-tls-verify'):
        raise RuntimeError('Kubernetes target identity mismatch')
    name = 'oci-native-identity-' + uuid.uuid4().hex[:10]
    probe = (ROOT / 'examples/native_object_storage.py').read_text()
    deny = """import runpy, oci
probe = runpy.run_path('/probe/probe.py', run_name='oci_probe_module')
try:
    probe['main']()
except oci.exceptions.ServiceError as error:
    if error.status in (403, 404) and error.code in ('NotAuthorizedOrNotFound', 'NotAuthorized', 'AuthorizationFailed', 'BucketNotFound'):
        print('PASS: unapproved service account denied artifact access')
    else:
        raise SystemExit('Unexpected service error in denial probe: status=' + str(error.status) + ' code=' + str(error.code))
else:
    raise SystemExit('FAIL: unapproved service account received artifact access')
"""
    cm = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name}, 'data': {
        'probe.py': probe, 'deny.py': deny}}
    job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name}, 'spec': {
        'backoffLimit': 0, 'activeDeadlineSeconds': 300, 'template': {'spec': {
            'restartPolicy': 'Never', 'serviceAccountName': 'default' if args.expect_denied else config['terraform']['workload_service_accounts'][0],
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 65534, 'runAsGroup': 65534, 'fsGroup': 65534, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'probe', 'image': IMAGE,
                'command': ['sh', '-ec', 'pip install --quiet --no-cache-dir --no-compile --target /tmp/deps oci==2.187.1 && PYTHONPATH=/tmp/deps python /probe/' + ('deny.py' if args.expect_denied else 'probe.py')],
                'env': [{'name': key, 'value': value} for key, value in {
                    'OCI_REGION': config['terraform']['region'], 'OCI_AUTH_MODE': 'workload',
                    'OCI_OBJECT_STORAGE_NAMESPACE': outputs['artifact_namespace']['value'],
                    'OCI_ARTIFACT_BUCKET': config['terraform']['artifact_bucket_name']}.items()],
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '50m', 'memory': '128Mi', 'ephemeral-storage': '256Mi'}, 'limits': {'cpu': '500m', 'memory': '512Mi', 'ephemeral-storage': '2Gi'}},
                'volumeMounts': [{'name': 'probe', 'mountPath': '/probe', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}]}],
            'volumes': [{'name': 'probe', 'configMap': {'name': name}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '1Gi'}}]}}}}
    try:
        run(['apply', '-f', '-'], json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': [cm, job]}))
        deadline = time.monotonic() + 300
        while True:
            status = json.loads(run(['get', 'job', name, '-o', 'json']))['status']
            if any(condition['type'] == 'Complete' and condition['status'] == 'True' for condition in status.get('conditions', [])):
                break
            if any(condition['type'] == 'Failed' and condition['status'] == 'True' for condition in status.get('conditions', [])) or time.monotonic() > deadline:
                raise RuntimeError('Native identity Job failed or expired')
            time.sleep(2)
        logs = run(['logs', 'job/' + name])
        expected = 'PASS: unapproved service account denied' if args.expect_denied else 'PASS: native principal authenticated'
        if expected not in logs:
            raise RuntimeError('Native identity roundtrip evidence missing')
        evidence = 'native-identity-denial-evidence.txt' if args.expect_denied else 'native-identity-evidence.txt'
        (ROOT / '.local' / evidence).write_text(logs)
        print('PASS: unapproved OKE service account denied artifact access' if args.expect_denied else 'PASS: live OKE workload identity wrote, read and deleted its own OCI Object Storage probe without static credentials')
    finally:
        logs = run(['logs', 'job/' + name], optional=True)
        (ROOT / '.local/native-identity-last-log.txt').write_text(logs)
        run(['delete', 'job', name, '--ignore-not-found'], optional=True)
        run(['delete', 'configmap', name, '--ignore-not-found'], optional=True)


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, RuntimeError):
        raise SystemExit('Native identity diagnostic failed; sanitized success evidence was not established')
