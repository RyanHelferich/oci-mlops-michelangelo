#!/usr/bin/env python3
"""Verify OCI-native artifact access using a workload or resource principal.

This is an integration diagnostic, not a replacement for Michelangelo's S3 client.
Required environment: OCI_REGION, OCI_OBJECT_STORAGE_NAMESPACE, OCI_ARTIFACT_BUCKET.
Optional: OCI_AUTH_MODE=workload (default) or resource_principal.
"""

from __future__ import annotations

import hashlib
import os
import uuid


def main() -> int:
    import oci

    required = ("OCI_REGION", "OCI_OBJECT_STORAGE_NAMESPACE", "OCI_ARTIFACT_BUCKET")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError("Missing required environment: " + ", ".join(missing))
    mode = os.environ.get("OCI_AUTH_MODE", "workload")
    if mode == "workload":
        signer = oci.auth.signers.get_oke_workload_identity_resource_principal_signer()
    elif mode == "resource_principal":
        signer = oci.auth.signers.get_resource_principals_signer()
    else:
        raise ValueError("OCI_AUTH_MODE must be workload or resource_principal")

    client = oci.object_storage.ObjectStorageClient(
        {"region": os.environ["OCI_REGION"]},
        signer=signer,
        timeout=(10, 60),
        retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
    )
    namespace = os.environ["OCI_OBJECT_STORAGE_NAMESPACE"]
    bucket = os.environ["OCI_ARTIFACT_BUCKET"]
    key = "_oci_wrapper_probe/" + uuid.uuid4().hex + ".txt"
    payload = b"OCI Michelangelo wrapper native identity probe\n"
    uploaded = False
    try:
        client.put_object(namespace, bucket, key, payload, content_type="text/plain")
        uploaded = True
        response = client.get_object(namespace, bucket, key)
        try:
            received = response.data.content
        finally:
            response.data.close()
        if hashlib.sha256(received).digest() != hashlib.sha256(payload).digest():
            raise RuntimeError("Artifact roundtrip digest mismatch")
        print("PASS: native principal authenticated and artifact roundtrip matched")
    finally:
        # Delete only the unpredictable key created by this invocation.
        if uploaded:
            client.delete_object(namespace, bucket, key)
            print("Probe object deleted; versioned buckets may retain an older version")
        client.base_client.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
