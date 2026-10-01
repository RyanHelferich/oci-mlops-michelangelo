"""Create schema-scoped MVP application credentials through a verified TLS Job.

Requires MYSQL_ADMIN_PASSWORD, MYSQL_PASSWORD, MYSQL_CA_FILE and private OKE
access. Temporary bootstrap credentials are removed even if the Job fails.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MYSQL_IMAGE = 'docker.io/library/mysql:8.4.6@sha256:869218921e61d6c3c89820955d63cca42971f0e3e6c1e2792247bbd944ebc6e9'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/config.json')
    parser.add_argument('--bootstrap', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    deployment, platform = config['deployment'], config['platform']
    if not args.bootstrap or not deployment['enable_mutations']:
        parser.error('Requires --bootstrap and deployment.enable_mutations=true')
    admin = os.environ['MYSQL_ADMIN_PASSWORD']
    password = os.environ['MYSQL_PASSWORD']
    user = platform['metadataStorage']['user']
    if user == config['terraform']['mysql_admin_username'] or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', user):
        raise RuntimeError('Application user must be a distinct safe SQL identifier')
    if not re.fullmatch(r'[A-Za-z0-9!+/=]{12,32}', password):
        raise RuntimeError('Use a generated 12-32 character application password containing only letters, digits, !+/=')
    kube = ['kubectl', '--context', deployment['kube_context'], '-n', deployment['namespace']]

    def run(arguments, payload=None, allow_failure=False):
        result = subprocess.run(kube + arguments, input=payload, capture_output=True, text=True)
        if result.returncode and not allow_failure:
            raise RuntimeError('Database bootstrap operation failed; inspect the temporary Job status locally')
        return result.stdout

    current = json.loads(run(['config', 'view', '--minify', '-o', 'json']))['clusters'][0]['cluster']
    if current['server'].rstrip('/') != deployment['kube_api_server'].rstrip('/') or current.get('insecure-skip-tls-verify'):
        raise RuntimeError('Kubernetes API identity mismatch')
    namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': deployment['namespace'],
        'labels': {'pod-security.kubernetes.io/enforce': 'baseline', 'pod-security.kubernetes.io/enforce-version': 'latest',
            'pod-security.kubernetes.io/warn': 'restricted', 'pod-security.kubernetes.io/audit': 'restricted'}}}
    run(['apply', '-f', '-'], json.dumps(namespace))
    name = 'mysql-bootstrap-' + uuid.uuid4().hex[:10]
    sql = '\n'.join(['CREATE DATABASE IF NOT EXISTS ' + db + ';' for db in ('michelangelo', 'temporal', 'temporal_visibility')])
    sql += f"\nCREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{password}';\n"
    sql += f"ALTER USER '{user}'@'%' IDENTIFIED BY '{password}';\n"
    sql += '\n'.join(f"GRANT ALL PRIVILEGES ON {db}.* TO '{user}'@'%';" for db in ('michelangelo', 'temporal', 'temporal_visibility'))
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name}, 'type': 'Opaque',
        'data': {key: base64.b64encode(value).decode() for key, value in {
            'admin-password': admin.encode(), 'bootstrap.sql': sql.encode(),
            'ca.pem': Path(os.environ['MYSQL_CA_FILE']).read_bytes()}.items()}}
    job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name}, 'spec': {
        'backoffLimit': 0, 'activeDeadlineSeconds': 300, 'template': {'spec': {
            'restartPolicy': 'Never', 'automountServiceAccountToken': False,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 999, 'runAsGroup': 999, 'fsGroup': 999, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'bootstrap', 'image': MYSQL_IMAGE,
                'command': ['sh', '-ec', 'mysql --ssl-mode=VERIFY_IDENTITY --ssl-ca=/bootstrap/ca.pem --host="$MYSQL_HOST" --user="$MYSQL_USER" < /bootstrap/bootstrap.sql'],
                'env': [{'name': 'MYSQL_PWD', 'valueFrom': {'secretKeyRef': {'name': name, 'key': 'admin-password'}}},
                    {'name': 'MYSQL_HOST', 'value': platform['mysqlRouter']['host']},
                    {'name': 'MYSQL_USER', 'value': config['terraform']['mysql_admin_username']}],
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}},
                'volumeMounts': [{'name': 'bootstrap', 'mountPath': '/bootstrap', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}]}],
            'volumes': [{'name': 'bootstrap', 'secret': {'secretName': name, 'defaultMode': 288}}, {'name': 'tmp', 'emptyDir': {}}]}}}}
    try:
        run(['apply', '-f', '-'], json.dumps(secret))
        run(['apply', '-f', '-'], json.dumps(job))
        run(['wait', '--for=condition=complete', 'job/' + name, '--timeout=300s'])
        print('Schema-scoped application user created over verified TLS; administrator is not installed in application pods.')
    finally:
        run(['delete', 'job', name, '--ignore-not-found', '--wait=true'], allow_failure=True)
        run(['delete', 'secret', name, '--ignore-not-found'], allow_failure=True)


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, RuntimeError):
        raise SystemExit('Database bootstrap failed; no credentials printed. Check configuration and local service status.')
