# Artifact identities

The MVP has two distinct access paths. The native OCI probe uses the OKE workload
identity policy created by Terraform. Upstream Michelangelo uses S3-compatible
authentication and therefore needs an explicitly approved customer secret key.
Neither a workload policy nor an instance principal changes upstream S3 clients.

## Dedicated S3 identity

An authorized tenancy identity administrator should perform this setup once:

1. Create a dedicated service user in the appropriate identity domain using an
   approved service email. Do not use a personal administrator's artifact key.
2. Create a dedicated group and add only that service user. Give the group no
   other roles or policies. Disable unnecessary interactive sign-in and credential
   capabilities using the domain's supported controls.
3. Put the group's OCID in the ignored central configuration as
   `terraform.artifact_s3_group_ocid`. Terraform creates a policy for the exact
   configured bucket, compartment and approved operations. Inspect the policy
   before deploying. Do not grant bucket creation/deletion or tenancy-wide access.
4. Create a customer secret key for the service user. Store its access key and
   secret in your secret manager or protected local storage outside Git. The
   cloud cannot retrieve the secret again after creation.
5. Set `deployment.static_object_storage_credentials_approved=true` only after
   approving this exception. Supply `AWS_ACCESS_KEY_ID` and
   `AWS_SECRET_ACCESS_KEY` through the process environment for Secret synchronization,
   then clear them. Use the existing Kubernetes Secret references in the chart.
6. Run the storage probe and pipeline example. Verify bucket listing is restricted
   and review all other policies attached to the user/group; permissions combine.

The optional example harness reads ignored `.local/artifact-customer-key.json`
with a `key` field containing the secret and an `id` field containing the access
key, matching the OCI CLI create response's `data` object. Protect the file with
owner-only permissions. This local convention is not a production secret manager.
Never print the file, paste it into configuration, or include it in support reports.

See [Oracle's customer secret key documentation](https://docs.oracle.com/en-us/iaas/Content/Identity/access/working-with-customer-secret-keys.htm)
for identity-domain-specific provisioning. User, group and key remain outside
this Terraform stack to keep credential material out of state. Remove or rotate
the key when retiring the deployment. Reviewed transient shutdown removes the
deployment policy; access resumes only after policy recreation, assuming no other
grants exist.

## Native workload identity

Align `workload_namespace` and `workload_service_accounts` with the actual
application namespace and service account. Terraform scopes the grant to the
exact OKE cluster, namespace, account and artifact bucket. Run
`python tools/test_native_identity.py --expect-denied` after establishing the
verified private API tunnel. It first proves approved access, then verifies that
the default service account cannot use the same bucket.

This probe does not replace upstream artifact authentication. Removing static
keys from controllers, Python, Ray and log collectors requires native adapters
and separate qualification. See [security](security.md) and [roadmap](../ROADMAP.md).
