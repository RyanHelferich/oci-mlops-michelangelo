"""Exercise the upstream MinIO SDK against the configured OCI artifact bucket.

Install minio==7.2.20 in the local renderer/test venv. Supply the approved scoped
AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY through the environment. A unique small
probe object is retained for inspection; never delete other objects.
"""
import argparse
import hashlib
import io
import json
import os
import uuid
from pathlib import Path
from minio import Minio
from minio.error import S3Error
from minio.commonconfig import Tags

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/config.json')
    parser.add_argument('--write-probe', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    if not args.write_probe or not config['deployment']['enable_mutations'] or not config['deployment']['static_object_storage_credentials_approved']:
        parser.error('Requires --write-probe, mutations enabled, and approved scoped S3 credentials')
    storage = config['platform']['objectStorage']
    if storage['secure'] is not True:
        raise RuntimeError('HTTPS is required')
    client = Minio(storage['endpoint'], access_key=os.environ['AWS_ACCESS_KEY_ID'],
        secret_key=os.environ['AWS_SECRET_ACCESS_KEY'], secure=True, region=storage['region'])
    key = '_oci_wrapper_probe/' + uuid.uuid4().hex + '.txt'
    payload = b'OCI Michelangelo artifact compatibility probe\n'
    checks = {'bucketExists': client.bucket_exists(storage['bucket'])}
    client.put_object(storage['bucket'], key, io.BytesIO(payload), len(payload), content_type='text/plain')
    response = client.get_object(storage['bucket'], key)
    try:
        checks['putGetChecksum'] = hashlib.sha256(response.read()).digest() == hashlib.sha256(payload).digest()
    finally:
        response.close()
        response.release_conn()
    checks['statSize'] = client.stat_object(storage['bucket'], key).size == len(payload)
    checks['listPrefix'] = key in [item.object_name for item in client.list_objects(storage['bucket'], prefix=key)]
    try:
        checks['bucketListScoped'] = all(bucket.name == storage['bucket'] for bucket in client.list_buckets())
    except S3Error as error:
        checks['bucketListScoped'] = error.code == 'AccessDenied'
    tags = Tags.new_object_tags()
    tags['oci-wrapper-probe'] = 'true'
    try:
        client.set_object_tags(storage['bucket'], key, tags)
        checks['objectTags'] = client.get_object_tags(storage['bucket'], key).get('oci-wrapper-probe') == 'true'
    except S3Error as error:
        checks['objectTags'] = error.code
    evidence = {'sdk': 'minio==7.2.20', 'checks': checks, 'retained_probe_key': key}
    (ROOT / '.local/object-storage-evidence.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps({'sdk': evidence['sdk'], 'checks': checks}, indent=2))
    if not all(checks[name] is True for name in ('bucketExists', 'putGetChecksum', 'statSize', 'listPrefix', 'bucketListScoped', 'objectTags')):
        raise RuntimeError('Required artifact compatibility or scope check failed')


if __name__ == '__main__':
    try:
        main()
    except S3Error as error:
        raise SystemExit('OCI S3 operation failed: ' + error.code)
    except (KeyError, ValueError, OSError, RuntimeError):
        raise SystemExit('Artifact probe failed; inspect local evidence without printing credentials')
