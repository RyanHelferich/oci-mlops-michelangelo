"""Create a short OCI Bastion session and print the private OKE tunnel command.

Uses the ignored central configuration and Terraform outputs. Run the printed
SSH command in a separate terminal; keep it open while using kubectl.
"""
import argparse
import json
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def run(arguments, cwd=None):
    result = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} failed; inspect the service status locally")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/config.json')
    parser.add_argument('--create-session', action='store_true')
    parser.add_argument('--outputs', type=Path, help='Optional local Terraform-compatible output export')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    tf = config['terraform']
    if not args.create_session or not config['deployment']['enable_mutations']:
        parser.error('Session creation requires --create-session and enable_mutations=true')
    outputs = json.loads(args.outputs.read_text(encoding='utf-8-sig')) if args.outputs else json.loads(run(['terraform', 'output', '-json'], ROOT / 'terraform'))
    get = lambda name: outputs[name]['value']
    endpoint = get('cluster_private_endpoint')
    parsed = urlsplit(endpoint if '://' in endpoint else 'https://' + endpoint)
    if not parsed.hostname or not get('bastion_id'):
        raise RuntimeError('Private cluster endpoint or Bastion output missing')
    local = ROOT / '.local/bastion'
    local.mkdir(parents=True, exist_ok=True)
    key = local / ('id_rsa_' + uuid.uuid4().hex[:10])
    run(['ssh-keygen', '-t', 'rsa', '-b', '4096', '-N', '', '-f', str(key)])
    oci = ['oci', '--profile', tf['config_file_profile'], '--region', tf['region']]
    target = json.loads(run(oci + ['ce', 'cluster', 'get', '--cluster-id', get('cluster_id')]))['data']
    if target['compartment-id'] != tf['compartment_ocid'] or target['endpoints']['private-endpoint'] != endpoint:
        raise RuntimeError('Cluster output identity does not match configured deployment')
    session = json.loads(run(oci + ['bastion', 'session', 'create-port-forwarding',
        '--bastion-id', get('bastion_id'), '--target-private-ip', parsed.hostname,
        '--target-port', str(parsed.port or 6443), '--ssh-public-key-file', str(key) + '.pub',
        '--session-ttl', '3600', '--wait-for-state', 'SUCCEEDED']))
    response = session['data']
    session_id = next((resource['identifier'] for resource in response.get('resources', [])
        if resource.get('entity-type') == 'SessionResource'), response['id'])
    if not session_id.startswith('ocid1.bastionsession.'):
        raise RuntimeError('Bastion did not return an identifiable session')
    session = json.loads(run(oci + ['bastion', 'session', 'get', '--session-id', session_id]))
    if session['data']['lifecycle-state'] != 'ACTIVE':
        raise RuntimeError('Bastion session is not active')
    session['data']['local-key-file'] = str(key)
    (local / 'session.json').write_text(json.dumps(session['data'], indent=2))
    kubeconfig = local / 'kubeconfig'
    run(oci + ['ce', 'cluster', 'create-kubeconfig', '--cluster-id', get('cluster_id'),
        '--file', str(kubeconfig), '--token-version', '2.0.0', '--kube-endpoint', 'PRIVATE_ENDPOINT'])
    context = run(['kubectl', '--kubeconfig', str(kubeconfig), 'config', 'current-context']).strip()
    cluster = json.loads(run(['kubectl', '--kubeconfig', str(kubeconfig), 'config', 'view', '-o', 'json']))['clusters'][0]['name']
    run(['kubectl', '--kubeconfig', str(kubeconfig), 'config', 'set-cluster', cluster,
         '--server=https://127.0.0.1:16443', '--tls-server-name=' + parsed.hostname])
    print('SSH tunnel (keep open; host key checking remains enabled):')
    print(f'ssh -i "{key}" -N -o ExitOnForwardFailure=yes -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -L 16443:{parsed.hostname}:{parsed.port or 6443} -p 22 {session_id}@host.bastion.{tf["region"]}.oci.oraclecloud.com')
    print(f'KUBECONFIG={kubeconfig}')
    print(f'Context: {context}; API server: https://127.0.0.1:16443')
    print('Set those exact context/server values in config/config.json before installation.')


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        raise SystemExit(str(error))
