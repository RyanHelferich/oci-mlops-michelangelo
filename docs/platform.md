# Platform installer

This wrapper preserves the upstream source and renders its pinned 0.11.0 Helm chart
for an OCI MySQL MVP. The installer uses Python 3.12+ standard library only; the
separate security post-renderer requires its pinned PyYAML dependency. This is a
development wrapper with deployment gates; production qualification remains
on the roadmap. See [validation guide](validation.md) and [pipeline checklist](pipeline-validation.md).

Use a dedicated OKE cluster. Live validation found that the pinned controller does
not implement the chart's namespace-watch setting. The wrapper uses its required
cluster-wide orchestration RBAC, removes namespace deletion and runtime CRD mutation
privileges, and installs a checksum-pinned bundle of 11 upstream-generated CRDs.
It disables API startup CRD synchronization. The UI runs non-root on port 8080
inside its pod with a read-only image filesystem and temporary Nginx storage;
its Service remains on port 80. Image short names are qualified for OCI CRI-O.

The original main revision was inspected for runtime/template compatibility; the
installer uses the release commit recorded in `upstream.lock.json` to
match verified published image digests. Every tracked checkout file is compared
against its pinned Git blob, allowing normal Git checkout newline conversion or
an exact raw blob. The upstream Bazel symlink is verified as link content and never
executed. Extra checkout files are excluded because the deployable chart is built
from `git archive` of the exact commit. Both chart dependency archives are SHA256
pinned, and every generated tracked chart file is checked again before installation.

For an ordered first deployment or rebuild, follow [deployment](deployment.md).
For private browser access, follow [access](access.md). These commands are run from
the repository root:

```text
python -m unittest discover -s tests -v
python scripts/installer.py --config config/config.example.json config validate
python scripts/installer.py --config config/config.json fetch
python scripts/installer.py --config config/config.json doctor
python scripts/installer.py --config config/config.json render
python scripts/installer.py --config config/config.json render --manifests
```

`doctor` reports available tools and verifies the source without calling OCI or
Kubernetes. `fetch` clones the exact source when absent, adds the two Helm repository
definitions, and prepares locked dependency packages in ignored `.generated/chart`.
Existing source at another revision is rejected, never reset automatically. An altered
generated chart is rejected; review/remove it locally and fetch again to recreate it.
`render` writes secret-free values and Terraform inputs; `--manifests` runs the TLS
post-renderer and creates the final Kubernetes manifest plus a review receipt.
Neither render command provisions infrastructure.

Install the renderer dependencies in a separate environment and set
`platform.mysqlRouter.python` to that environment's Python executable:

```text
python -m venv .local/renderer-venv
.local/renderer-venv/Scripts/python.exe -m pip install -r security/mysql-router/requirements.txt
```

On Linux/macOS the executable is `.local/renderer-venv/bin/python`. Helm must be on
PATH; on Windows it can also be placed at the ignored local
`.local/bin/helm/windows-amd64/helm.exe`. The wrapper invokes Python as Helm's native
post-renderer executable with the script as an argument, avoiding shell quoting and
platform-specific executable shims.

Infrastructure planning requires explicit read authorization and acknowledgement
that local Terraform artifacts can contain credentials:

```text
python scripts/installer.py --config config/config.json terraform plan --allow-cloud-read --ack-sensitive-local-artifacts
```

Review the saved `.generated/deployment.tfplan` privately with `terraform show`.
Protect the plan, provider cache, and Terraform state with local access controls;
Terraform `sensitive` metadata does not encrypt those artifacts. Console and
subprocess diagnostics are withheld because Terraform and Kubernetes can include
credential values. Existing plan files are never silently overwritten.

Only after deployment is authorized, and with `enable_mutations=true` already set
in the reviewed configuration, apply the exact saved plan:

```text
python scripts/installer.py --config config/config.json terraform apply --allow-paid-resources --ack-sensitive-local-artifacts --confirm-target <compartment-ocid> --reviewed-plan-sha256 <reviewed-sha256>
```

The wrapper compares the plan, configuration, and Terraform source hashes before
applying. It does not run a replacement plan during apply. Development shutdown has a separate reviewed destroy plan and permission; see [operations](operations.md).
Changing configuration or source requires a fresh reviewed plan. Inventory existing
resources and review budget/backup/storage costs in `docs/infrastructure.md` before
authorizing apply. A development size guardrail does not prove a monthly budget.

After infrastructure exists, copy actual outputs into local platform settings:
`object_storage_s3_endpoint`, `artifact_namespace`, bucket name, MySQL DNS name/port,
and the expected private OKE API server. Use `mysql_expected_hostname` for the SAN
and Router/Temporal `serverName`; never substitute an IP or turn off verification.
OCI system certificates require an independently verified trust/identity route;
the configured BYOC route must have an appropriate certificate and scoped service
access policy. The installer does not create or import an OCI server certificate; the separate
[dev helper](development-certificates.md) can prepare/import one before planning.

Establish a VPN/tunnel route to the private Kubernetes API, obtain kubeconfig out of
band, and create the release namespace. The installer verifies the exact configured
context/API server, certificate verification setting, Kubernetes >=1.29, and four
existing Secrets with the required keys. The cluster version gate is required by
the restartable Router init sidecars, which start before schema initialization.

For the first development installation, use `tools/bootstrap_database.py --bootstrap`
after configuring private OKE access. Supply `MYSQL_ADMIN_PASSWORD`, a distinct generated
`MYSQL_PASSWORD`, and the public CA path in `MYSQL_CA_FILE`. Set
`platform.metadataStorage.user` to `michelangelo_app`, distinct from the Terraform
provisioning administrator. The helper creates `michelangelo`, `temporal`, and
`temporal_visibility` and grants application privileges only on those schemas.
It uses verified remote TLS, then removes the temporary administrator Secret and Job.
The namespace enforces baseline Pod Security and warns/audits restricted requirements;
full restricted enforcement needs additional upstream pod compatibility validation.
Production should separate metadata and workflow database identities.

`platform.metadataStorage` points exclusively at localhost Router. The post-renderer
adds Router to both apiserver and controllermgr, preserving the upstream templates.
Router verifies remote MySQL certificates and hostname. The dedicated frontend RSA
certificate/key enables cold `caching_sha2_password` authentication for the upstream
plaintext loopback client. Database traffic outside the pod uses verified TLS.
Warm authentication-cache success alone is insufficient to attest this transport.

Bundled Temporal runs one replica of each of its four services with MySQL default
and visibility stores. Both normal service connections and schema jobs receive
the CA mount and SQL TLS/hostname settings. Cassandra, Elasticsearch, Prometheus,
Grafana, Temporal web, and its standalone admintools pod are disabled. Schema jobs
and namespace registration still use the admin-tools image. Dev uses 16 history
shards; production configuration keeps the subchart's 512. **Do not change history
shard count on an existing Temporal database.** Review upgrade/schema job behavior
before upgrading an existing release.

With the final manifest reviewed and both MVP exceptions plus runtime TLS evidence
recorded in local config:

```text
python scripts/installer.py --config config/config.json install --allow-cluster-mutation --confirm-target <kube-context> --reviewed-manifest-sha256 <reviewed-sha256>
python scripts/installer.py --config config/config.json smoke
```

Optional `--sync-secrets` on install explicitly creates/updates the configured Secrets
from environment and certificate files described in `config/README.md`; it streams
JSON through stdin and creates no credential file. It does not create a namespace,
customer key, database user, or CA. Without that flag, existing Secrets are mandatory.
Helm uses `--atomic --wait` and an explicit timeout. The receipt prevents unnoticed
changes to chart packages, values, renderer, config, or the reviewed manifest.
Before a bundled Temporal upgrade, the installer recreates only the two completed
setup Jobs owned by this release, because Kubernetes Job pod templates are immutable.
It refuses to remove active or unsuccessful setup Jobs and never selects training
Jobs. Review database schema migrations before changing the upstream release.

Smoke checks core and bundled Temporal Deployment rollouts plus namespace-job
completion. These checks do not execute a training pipeline, verify artifact I/O,
test application authentication, or prove database permissions/certificate validity.
The upstream deployments do not supply full application readiness probes. Perform
functional workflow and object-storage checks before using real data. UI/Envoy and
API remain ClusterIP; access the UI through localhost port forwards. No public
Ingress, application authentication, pod network isolation, backup restoration test,
or complete production security posture is supplied by this installer.

The optional actual Helm integration renders the exact release chart through
`deploy/postrenderer.py` and checks Router sidecars, Temporal schema setup, both
SQL stores, namespace registration and absence of rendered Secrets. Set
`INSTALLER_RUN_HELM_INTEGRATION=1` before running the wrapper test suite.
The wrapper normalizes two known duplicate metadata label pairs in the upstream
namespace Job; other duplicate YAML mappings remain rejected.
