# Deploy or rebuild the development MVP

Run commands from the repository root. This guide provisions resources in a
customer-selected OCI tenancy and dedicated compartment. Review resource shapes,
permissions and retention before deployment. Rebuilding creates a new cluster
and database unless a separate recovery procedure is used.

## 1. Prerequisites

| Requirement | Tested baseline / purpose |
| --- | --- |
| OCI tenancy and operator profile | Authenticated `DEFAULT` profile; permission to manage the dedicated deployment resources and scoped policies |
| Dedicated compartment | Create/select before planning; keep unrelated resources out of the deployment state |
| Region | Installer supports Ashburn or Phoenix; qualify capacity and deployment in the selected region |
| Tools | Python 3.12, Git, Terraform 1.9.8, Helm 3.19.0, kubectl, OCI CLI, OpenSSH; OpenSSL for development certificates |
| Capacity | Region-compatible x86 OKE image, supported Kubernetes version and exact availability-domain names; service limits and capacity verified in your tenancy |
| Connectivity | Private route/VPN or OCI Bastion with your public `/32`; outbound image/package access |
| Protected local storage | State/plans, credentials, keys and `.local` need OS access controls in addition to Git exclusions |

Docker is needed only if you reproduce the local TLS runtime qualification.
The installer performs no automatic tool installation. Keep the OCI CLI's Python
dependencies separate from the renderer and pipeline environments.

```text
git clone <repository-url>
cd oci-mlops-michelangelo
```

Copy `config/config.example.json` to `config/config.json` using your shell or
editor. Configure only the local copy; the example is not ready to deploy.

## 2. Configure infrastructure and identities

Read [the configuration reference](../config/README.md). Set:

- Tenancy/compartment IDs, profile, region, OKE image, Kubernetes version and AD.
- Dev shapes/counts and nonoverlapping VCN CIDR. For private operator access set
  `enable_bastion=true` and `bastion_client_cidrs=["<your-public-ip>/32"]`.
  An `admin_cidrs` rule alone does not create a private route from the operator network.
- Unique artifact bucket name; match it in `platform.objectStorage.bucket`.
- Application namespace/service account, matching the rendered chart.
- An existing approved S3 group, per [artifact identity setup](artifact-identity.md).
  The service user/key is provisioned outside Terraform.
- Required MySQL certificate, CA trust and matching DNS name. For disposable dev,
  use [the development certificate helper](development-certificates.md) before
  planning. Set `enable_mutations=true` before its `--import-oci` operation and
  verify the imported certificate becomes `ACTIVE`.
- MySQL backup retention and deletion choices. The example retains automatic
  backups and requires a final backup; these can outlive the database.

Approve `mysql_mvp_approved` and `static_object_storage_credentials_approved`
only after accepting the documented compatibility choices. `router_tls_verified`
is a transport-test attestation, not a switch that performs verification. Read
[database transport](database-transport.md), inspect the locally generated runtime evidence
and repeat qualification when changing that transport.

Supply a generated database administrator password through
`TF_VAR_mysql_admin_password` from protected storage or your secret manager.
Do not pass it in command arguments. Keep it available for both plan and apply.

## 3. Prepare and verify the pinned source

```text
python -m venv .local/renderer-venv
```

Install dependencies using the environment's interpreter:

| Shell / OS | Command |
| --- | --- |
| Windows | `.local/renderer-venv/Scripts/python.exe -m pip install -r security/mysql-router/requirements.txt` |
| Linux/macOS | `.local/renderer-venv/bin/python -m pip install -r security/mysql-router/requirements.txt` |

Set `platform.mysqlRouter.python` to that executable's absolute path. Then:

```text
python scripts/installer.py config validate
python scripts/installer.py fetch
python scripts/installer.py doctor
```

`fetch` needs network access. `doctor` is local and verifies the fetched source,
so run it after `fetch` on a fresh clone. It reports tool availability; resolve
missing tools before continuing. Detailed chart preparation is in
[platform installation](platform.md).

## 4. Plan and apply infrastructure

Set `deployment.enable_mutations=true` before creating the review receipt.
Archive any previous `.generated/deployment.tfplan` privately; the installer
refuses to overwrite it. Preserve existing Terraform state when rebuilding so
the retained artifact bucket remains managed.

```text
python scripts/installer.py terraform plan --allow-cloud-read --ack-sensitive-local-artifacts
terraform -chdir=terraform show ../.generated/deployment.tfplan
python scripts/installer.py terraform apply --allow-paid-resources --ack-sensitive-local-artifacts --confirm-target <compartment-ocid> --reviewed-plan-sha256 <printed-plan-sha256>
```

Review shapes, resource counts, private endpoints, IAM, retention and costs.
Plan output can include sensitive values; do not publish it. Do not run a separate
replacement plan immediately before apply. The installer applies the reviewed
saved plan only. See [infrastructure](infrastructure.md).

## 5. Establish access and refresh endpoint settings

Follow [private access](access.md) to create a Bastion session and keep its SSH
command running. Set the isolated `KUBECONFIG` in each terminal that calls the
installer/bootstrap tools. Copy the exact context and API URL into central config.

Read `terraform -chdir=terraform output -json` privately. For the native identity
probe, also save a fresh ignored output snapshot; the snapshot contains resource
identifiers and must not be committed:

```powershell
# Windows PowerShell
terraform -chdir=terraform output -json | Out-File .local/terraform-outputs.json -Encoding utf8
```

```bash
# Linux/macOS
terraform -chdir=terraform output -json > .local/terraform-outputs.json
```

Update:

| Output | Configuration |
| --- | --- |
| `object_storage_s3_endpoint` | `platform.objectStorage.endpoint` |
| `artifact_bucket_name` | `platform.objectStorage.bucket` |
| `mysql_expected_hostname` | `platform.mysqlRouter.host` (same identity Temporal uses) |
| `mysql_endpoint` port | `platform.mysqlRouter.port` |
| Helper's context / tunneled API | `deployment.kube_context` / `deployment.kube_api_server` |

Keep `platform.metadataStorage.host=127.0.0.1` and port `6446`; these refer to
Router inside each client pod, not the remote database. Do not reuse stale
outputs, kubeconfig or sessions after a rebuild. Keep
`platform.ui.apiBaseUrl=http://127.0.0.1:8081` for the documented access path.

## 6. Bootstrap database and prepare Secrets

Provide `MYSQL_ADMIN_PASSWORD`, a distinct generated `MYSQL_PASSWORD`, and
`MYSQL_CA_FILE`. The bootstrap application password must be 12-32 characters
using letters, digits and `!+/=`; its helper rejects other characters. Set
`platform.metadataStorage.user` to the distinct application user, such as
`michelangelo_app`.

```text
python tools/bootstrap_database.py --bootstrap
```

The helper creates the namespace with baseline Pod Security, verifies remote
SQL TLS/hostname, creates three schemas and grants the application user privileges
only on those schemas. It removes its temporary administrator Job/Secret.
Remove `MYSQL_ADMIN_PASSWORD` from the environment afterward.

Prepare a **separate** Router RSA certificate/key. For first-time dev setup in
an unused directory, with OpenSSL on PATH:

```text
python -c "from pathlib import Path; Path('.local/pki/router').mkdir(parents=True, exist_ok=True)"
openssl req -x509 -newkey rsa:4096 -sha256 -nodes -days 30 -subj "/CN=oci-michelangelo-router" -keyout .local/pki/router/tls.key -out .local/pki/router/tls.crt
```

Do not overwrite existing keys during an ordinary upgrade. Protect the private
key with OS permissions and renew deliberately. The database server private key
must never be used as Router's key or mounted into Kubernetes.

Provide all six [Secret synchronization inputs](../config/README.md):
`MYSQL_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `MYSQL_CA_FILE`,
`MYSQL_ROUTER_AUTH_CERT_FILE`, `MYSQL_ROUTER_AUTH_KEY_FILE`. The two Router file
variables point to the separate files above. Alternatively provision the four
existing Secrets out of band. No secret values are rendered into Helm manifests.

## 7. Review and install the platform

Finish configuration changes before this step. A changed config invalidates the
manifest receipt. With required transport attestations recorded:

```text
python scripts/installer.py render --manifests
python scripts/installer.py install --sync-secrets --allow-cluster-mutation --confirm-target <exact-kube-context> --reviewed-manifest-sha256 <printed-manifest-sha256>
python scripts/installer.py smoke
```

Review `.generated/manifest.yaml` before install. Omit `--sync-secrets` if all
referenced Secrets already exist. Helm waits with atomic rollback; this does not
roll back database schema changes. Clear password/key environment variables
when the required operations are complete.

## 8. Access, use and validate

Start both local UI/API forwards from [the access guide](access.md), then open
`http://127.0.0.1:8080`. Run [the regression example](../examples/mvp-pipeline/README.md)
for an end-to-end check, plus [artifact/identity probes](validation.md#repeat-the-live-probes)
as needed. The example uses independent local API/Temporal ports and stops its
own forwards. A successful smoke command alone does not prove a workflow.

Retain sanitized evidence, review actual costs, and use
[guarded transient shutdown](operations.md#teardown) when the dev session ends.
For a rebuild, repeat endpoint/bootstrap/install steps against the newly created
resources; restoring old model/project metadata from backup is separate work.
