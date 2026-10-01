# Validation guide

Use these checks to qualify a deployment in the target environment. Public
reports should describe software versions and aggregate results only. Keep
resource identifiers, account details, endpoints, credentials and execution
records in protected local storage, not this repository.

## Offline checks

```text
python -m unittest discover -s tests -v
python -m unittest discover -s security/mysql-router -p test_post_renderer.py -v
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false -input=false -lockfile=readonly
terraform -chdir=terraform validate
terraform -chdir=terraform test
```

The Terraform suite uses a mock provider. Offline checks do not prove regional
capacity, IAM acceptance, application functionality or recovery. To include the
actual pinned chart render, fetch upstream first, install the renderer environment
and set `INSTALLER_RUN_HELM_INTEGRATION=1` before the wrapper test command.

## Deployment acceptance

| Layer | Required verification |
| --- | --- |
| Target | Exact profile, region, compartment, cluster context and API identity |
| Infrastructure | Private endpoints, intended shapes, Ready nodes, scoped IAM and no unexpected public access |
| Database | Verified hostname/CA, schema-scoped app user and removal of temporary administrator credentials |
| Platform | Core/Temporal rollouts and setup Jobs; upstream CRDs installed by the operator |
| Artifacts | HTTPS checksum roundtrip, stat/list/tag compatibility and correct bucket scope |
| Native identity | Approved workload can access the bucket; unapproved account cannot |
| Pipeline | Controller-started workflow, storage activity, expected result and Model lineage |
| Lifecycle | Reviewed upgrade/shutdown and inventory of retained storage/backups |

## Repeat the live probes

With an isolated kubeconfig, verified private API route, approved configuration,
fresh `.local/terraform-outputs.json` snapshot and
`terraform.artifact_allow_delete=true` for probe cleanup:

```text
python tools/test_native_identity.py
python tools/test_native_identity.py --expect-denied
```

The denial command first checks the approved account, then verifies the default
service account cannot use the same bucket. The helper removes its Job/ConfigMap
and probe object; older versions may remain in a versioned bucket. It installs
the pinned OCI SDK inside a temporary pod and needs outbound package access.

For the upstream S3 client, install `minio==7.2.20` in an isolated environment,
supply the approved key through `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`,
then run `python tools/test_object_storage.py --write-probe`. Clear the variables
afterward. Its uniquely named small object is retained for inspection.

Run [the regression example](../examples/mvp-pipeline/README.md) and use
[the pipeline checklist](pipeline-validation.md) to inspect its evidence. Ready
pods alone are insufficient. Diagnostic output belongs in ignored `.local`.

## Transport and production gates

Reproduce [the local TLS probe](database-transport.md) for cold authentication and
wrong-CA/hostname/no-TLS rejection. It does not substitute for deployment checks.
Ray/Spark/GPU training, serving, application authentication/authorization, tenant
isolation, HA, recovery and upgrades require additional qualification. See
[compatibility](compatibility.md), [security](security.md) and [roadmap](../ROADMAP.md).
