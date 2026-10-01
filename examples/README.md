# Native OCI identity diagnostic

`native_object_storage.py` verifies Object Storage put/get/delete using an OKE workload identity or a supported OCI resource principal. It does not load an OCI configuration file, accept an API private key, or fall back to operator credentials.

Install the pinned SDK in a workload image, run in the exact namespace and service account authorized by the Terraform IAM policy, and set:

```text
OCI_AUTH_MODE=workload
OCI_REGION=<deployment region>
OCI_OBJECT_STORAGE_NAMESPACE=<namespace from local configuration>
OCI_ARTIFACT_BUCKET=<artifact bucket>
```

Then run `python native_object_storage.py` inside the pod. The workload needs bucket-scoped object create/read/delete permissions. The diagnostic creates an unpredictable object under `_oci_wrapper_probe/` and deletes only that object. A versioned bucket may retain its earlier version; preserve or clean versions according to the bucket's lifecycle policy.

For a supported resource-principal environment outside OKE, use `OCI_AUTH_MODE=resource_principal` and its corresponding IAM policy. Do not use this mode as a substitute for OKE workload identity.

This probe demonstrates the intended native authentication boundary. A passing probe does **not** prove that upstream Michelangelo S3 clients use this signer. Their native adapter integration remains on the roadmap.

Sources: [OCI Python signing documentation](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/signing.html), [OKE workload access](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contenggrantingworkloadaccesstoresources.htm).
