# Operations

Use [deployment](deployment.md) to provision and [access](access.md) to connect.
Retained artifacts and backups provide storage/recovery, not a running platform.

## Build session lifecycle

1. Verify the selected OCI profile, region and dedicated compartment.
2. Run configuration validation and tool preflight.
3. Render configuration and obtain a saved Terraform plan.
4. Review the planned resource counts, shapes, public addresses, IAM policies and cost.
5. Apply that exact plan. Keep Terraform state and credentials private.
6. Establish the private Kubernetes API access path, verify the cluster identity and refresh endpoint/context configuration.
7. Bootstrap the application database schemas/user, then create or synchronize required Secrets, including credentials and CA trust.
8. Install the pinned upstream chart with the OCI wrapper.
9. Verify services, database TLS, artifact operations and a real ML pipeline.
10. Preserve validation evidence and required data, then stop or destroy disposable resources.

The exact CLI commands are documented in [the platform guide](platform.md) and [configuration reference](../config/README.md). Never substitute a successful `helm template` or a Ready pod for a successful pipeline.

## Verify a deployment

For a stack configured with OCI Bastion, run `python tools/connect_bastion.py --create-session` after Terraform completes. The helper creates a one-hour forwarding session and an isolated kubeconfig under `.local/bastion`. Run its printed SSH command in a separate terminal, set `KUBECONFIG` to the printed file, and copy the exact context and local API URL into the central configuration. The helper preserves Kubernetes CA verification and uses the private endpoint as the TLS server name. SSH host key verification remains enabled. Renew expired sessions explicitly and stop the SSH process when the session ends.

Record the source revision, chart version, image digests, OKE version, MySQL version, node capacity and workflow backend. Verify:

- The private API is reachable only through the intended operator access path.
- The API and controller reach MySQL through loopback Router sidecars.
- Backend TLS requires a trusted CA and matching certificate identity.
- A wrong CA/server name causes connection failure.
- Metadata schemas and workflow history/visibility schemas exist.
- Artifact write/read/list/delete works against OCI Object Storage over HTTPS.
- A workflow reaches completion and its model/artifact can be fetched.
- UI/API access is limited to the authorized test users.

## Logging

Check which log types the configuration enables. OCI Object Storage service logs, Kubernetes pod logs, Temporal workflow history and model prediction logs are separate. Kubernetes logs can be inspected while the cluster is running; durable OCI log collection needs its own configured collector and IAM permissions.

Never retain sensitive training data or credentials in deployment reports. Redact identifiers from public evidence and keep full tenancy diagnostics in local notes.

## Backup and recovery boundaries

Terraform creates MySQL backup policy and the configured final backup on deletion.
Object Storage versioning preserves artifact versions; it does not preserve the
MySQL project/model registry or Temporal history. Keep both data sets and local
state/configuration if you need to resume a prior environment.

A regular infrastructure reapply creates a fresh database. It does not automatically
restore the retained backup. No restore/failover drill has passed for this MVP.
Before relying on recovery, rehearse restoring a backup into a separate database,
verify certificate/grants, reconcile the restored resource with reviewed Terraform
state/configuration, and prove platform metadata/workflow/artifact consistency.
Preserve the source backup until validation succeeds. Treat this as a production
release gate, not a supported one-command recovery feature.

The example config requests 1-day automatic retention; the Terraform default is
7 days. Inspect the actual cloud backup expiry/size after deletion;
final retention is not controlled by the example's automatic-retention field.

## Backup and upgrade

Before upgrades, preserve database recovery points, artifacts, configuration and release metadata. Test source/chart upgrades in a separate disposable environment. Review schema changes and rollback limitations; Helm rollback cannot reverse database migrations or restore deleted model objects.

Rotate artifact customer secret keys using an overlap period, synchronize the new Kubernetes Secret, restart affected workloads and prove access before deleting the old key. Validate the behavior of dynamically created Ray/job pods as well as long-lived control-plane pods.

## Teardown

For a disposable **dev** stack, use the guarded transient shutdown. It removes
the cluster, worker, database, networking and deployment policies while keeping
the versioned artifact bucket in Terraform state. The configured MySQL deletion
policy determines final/retained backups; review it before proceeding. This is
not a database pause: the next apply creates a new database and cluster, and
restoring prior metadata requires a separate restore procedure.

Archive the preceding saved plan locally before planning again. With the
database administrator password supplied through `TF_VAR_mysql_admin_password`:

```text
python scripts/installer.py terraform plan --allow-cloud-read --ack-sensitive-local-artifacts --shutdown-transient
terraform -chdir=terraform show ../.generated/deployment.tfplan
python scripts/installer.py terraform apply --allow-shutdown --ack-sensitive-local-artifacts --confirm-target <dedicated-compartment-ocid> --reviewed-plan-sha256 <printed-sha256>
```

The installer rejects shutdown plans that delete any Object Storage bucket or
contain creation/update actions. The separate shutdown permission cannot
authorize an ordinary deployment. Review deletions privately because plan/state
output may expose sensitive values. Save local evidence and Terraform state.
On the next build session, create and review an ordinary plan, explicitly allow
paid resources, bootstrap the new database, regenerate endpoint references and
reinstall the platform. Do not reuse stale cluster outputs or kubeconfig.

The dedicated compartment, external service identity/key and development
certificate may remain outside Terraform. Bucket and backup storage can still
incur charges. Removing the scoped deployment policies revokes the artifact
service identity's access until the policies are recreated.

Destroy only resources belonging to this deployment. Review data retention first: Terraform may intentionally refuse to delete a nonempty artifact bucket. Versioned buckets include old versions and delete markers. Review workflow and MySQL backups separately.

After teardown, list clusters, node pools, compute instances, boot/block volumes, databases, backups, buckets, log groups and access sessions in the deployment compartment. Record retained resources and expected charges. The dedicated compartment may remain for later sessions; an empty compartment is not evidence that backups elsewhere were removed.

Do not delete the parent development compartment or modify other projects in the tenancy.
