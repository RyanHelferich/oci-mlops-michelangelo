# OCI infrastructure

This Terraform stack builds a private OKE Enhanced cluster and its supporting network in an **existing dedicated compartment**. Applying a reviewed plan creates the OCI resources; install Kubernetes application workloads separately using the platform installer. The toolchain uses Terraform 1.9.8 and the signed, locked `oracle/oci` 9.8.0 provider. See [validation](validation.md) for qualification steps.

The Terraform default is `database_mode = "none"`. The approved MySQL MVP can explicitly select `"mysql"`; Autonomous Database is outside this stack because upstream `go/storage/mysql/mysql.go` uses `github.com/go-sql-driver/mysql`, MySQL SQL and a MySQL DSN. Helm's PostgreSQL setting does not establish Go backend compatibility.

Follow [deployment](deployment.md) for the ordered setup and [access](access.md)
for the private operator route.

## Resources and access boundaries

| Component | Behavior |
| --- | --- |
| VCN | Private IPv4 /16; no internet gateway or public subnets |
| Regional subnets | API .0/24, workers .1/24, private LB .2/24, database .3/24; optional Bastion .4/24, derived from `vcn_cidr` |
| Routing | API/workers use the regional all-services gateway and optional outbound NAT; LB/database/Bastion have local VCN routing only |
| Security | Empty baseline security list; separate API, worker, LB and DB NSGs; Bastion subnet has only API TCP/6443 egress |
| OKE | `ENHANCED_CLUSTER`, private API, flannel overlay, dashboard/Tiller disabled |
| Node pool | x86 E4 Flex by default, 2 OCPUs/16 GiB, 50 GiB boot volumes, encryption in transit; explicit region-compatible OKE image required |
| Artifacts | Private Standard Object Storage bucket with versioning; no automatic data purge |
| Workload identity | Exact cluster, namespace and service-account conditions, restricted to the named bucket and explicit operations |
| Optional MySQL | `MySQL.2` standalone dev or explicitly enabled HA prod, private NSG TCP/3306 from workers only, TLS required, automatic backups |
| Optional Bastion | Oracle-managed API TCP forwarding, explicit public operator /32 allowlist, maximum one-hour sessions; no jump VM or permanent SSH keys |
| Optional logs | Bucket read/write service logs and subnet flow logs, default 30-day retention; application logging is separate |

Flannel needs worker-to-worker traffic and API-to-worker TCP for cluster operation; these broad rules are bounded by NSG membership. Workers reach the API on TCP/6443 and 12250, Oracle services on TCP, the resolver/NTP link-local address on their specific ports, and NAT-backed internet destinations on TCP/80 and 443. ICMP type 3/code 4 permits path-MTU discovery. Rules follow [Oracle's OKE network requirements](https://docs.oracle.com/en-us/iaas/Content/ContEng/Concepts/contengnetworkconfig.htm).

The private LB subnet reserves space for a later Kubernetes service. This stack creates no load balancer. If one is deployed, attach the `lb` NSG, request an **internal** OCI LB, disable automatic security-list management, and use TLS/443. The reserved NSG permits NodePort TCP/30000-32767 and kube-proxy health TCP/10256 to workers; only configured `admin_cidrs` can enter the LB on 443. Chart annotations and backend health ports must be reviewed against the actual service.

`admin_cidrs` allows API access from specified routed sources; it does not create VPN, peering, FastConnect, DRG attachments or routes. With no private route, enable Bastion and supply `bastion_client_cidrs`. After an approved deployment, generate private-endpoint kubeconfig using `kubeconfig_command`, create a port-forwarding session targeting the API's private IP on 6443, and use the returned SSH command with a loopback listener. Keep certificate authority verification enabled; if the local tunnel needs a different server address, set kubeconfig `tls-server-name` to the original API identity. Sessions and keys are intentionally not persisted in Terraform. See [OKE Bastion access](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengsettingupbastion.htm).

## Configuration contract

The central `config/config.example.json` `terraform` object maps directly to these flat variables. Terraform does not read an independent copy of the central configuration. `terraform/variables.tf` and `terraform/outputs.tf` are authoritative. Example tfvars are illustrative and contain placeholders, never tenancy-specific values or secrets.

| Inputs | Defaults / requirement |
| --- | --- |
| `tenancy_ocid`, `compartment_ocid` | Required existing identifiers; no compartment creation |
| `region`, `config_file_profile` | `us-ashburn-1`, `DEFAULT`; credentials remain in the OCI profile/provider environment |
| `project_name`, `environment` | `michelangelo`, `dev`; environment must be dev/prod |
| `kubernetes_version`, `node_image_ocid`, `availability_domains` | `v1.34.10`; image and exact AD names required; verify region support and image architecture/version before a real plan |
| `node_shape`, `node_count`, `node_ocpus`, `node_memory_gbs`, `node_boot_volume_gbs` | E4 Flex; null count means dev 1/prod 3; 2/16/50; prod requires three nodes and three ADs |
| `vcn_cidr`, `admin_cidrs`, `enable_nat_gateway` | `10.42.0.0/16`, empty, true; fixed overlay pods `10.244.0.0/16`, services `10.96.0.0/16` must not overlap any routed networks |
| `artifact_bucket_name`, `workload_namespace`, `workload_service_accounts` | Bucket required; `michelangelo`, `["michelangelo-artifacts"]`; align with rendered chart service accounts |
| `artifact_allow_delete`, `artifact_s3_group_ocid` | false, null; existing approved S3 group may receive an optional restricted exception policy |
| `enable_logging`, `log_retention_days` | false, 30; supported retention multiples of 30 through 180 |
| `enable_bastion`, `bastion_client_cidrs` | false, empty; enabling requires explicit operator public /32 addresses |
| `database_mode`, `mysql_shape`, `mysql_storage_gbs`, `mysql_high_availability` | none, MySQL.2, 100, false; prod MySQL requires HA |
| `mysql_admin_username`, `mysql_admin_password` | `ma_admin`; password required only for mysql, injected via `TF_VAR_mysql_admin_password` |
| `mysql_hostname_label`, `mysql_certificate_ocid`, `mysql_certificate_compartment_ocid` | mysql, null, null; existing BYOC cert, compartment defaults to deployment compartment |
| `mysql_backup_retention_days`, `mysql_backup_retention_on_delete`, `mysql_final_backup_on_delete` | 7, RETAIN, REQUIRE_FINAL_BACKUP; prod requires preservation |
| `tags` | Empty optional map merged with Project/Environment/ManagedBy attribution |

Outputs include `cluster_id`, `cluster_private_endpoint`, `node_pool_id`, `vcn_id`, `subnet_ids`, `network_security_group_ids`, `artifact_bucket_name`, `artifact_namespace`, `object_storage_s3_endpoint`, `workload_identity`, `artifact_s3_policy_id`, `mysql_endpoint`, `mysql_expected_hostname`, `bastion_id`, `bastion_private_endpoint_ip`, `log_group_id` and `kubeconfig_command`. `mysql_endpoint` is null with no database; otherwise it contains ID, remote host/IP/port, `tls_only` and nullable certificate OCID. Secret passwords are never outputs. S3 endpoint construction targets commercial OC1 regions only.

## Database and artifact compatibility gaps

MySQL.2 currently supplies 2 ECPUs and 16 GiB; no HeatWave analytical cluster is created. Custom configuration copies the selected shape's HA or Standalone default options, then sets `require_secure_transport=ON`. [Current supported shapes](https://docs.oracle.com/en-us/iaas/mysql-database/doc/supported-shapes.html) and [configuration reference](https://docs.oracle.com/en-us/iaas/tools/terraform-provider-oci/latest/docs/r/mysql_mysql_configuration.html) describe those choices.

Upstream's DSN has no TLS option. Pointing it directly at this DB fails rather than accepting plaintext. Application deployment must supply a loopback transport wrapper with remote certificate validation, or modify the client to negotiate TLS. The current Router experiment has a **blocking cold `caching_sha2_password` authentication incompatibility** when its client leg is plaintext; do not rely on a warmed authentication cache, downgrade authentication or disable server TLS. Terraform cannot resolve that application transport issue.

Supplying `mysql_certificate_ocid` selects BYOC with a certificate whose SAN must match `mysql_expected_hostname` (defaults to `mysql.db.madev.oraclevcn.com`). The returned live endpoint must be checked against this expected DNS name. Certificate import, trust-chain distribution, private-key custody, renewal and CA Kubernetes Secret creation are separate operations. Null chooses service-defined SYSTEM TLS; its self-signed certificate does not establish verified server identity and is unsuitable for claiming the secure MVP transport is complete. See [Oracle's BYOC guidance](https://blogs.oracle.com/mysql/introducing-bring-your-own-certificate-byoc-in-mysql-heatwave-service).

The certificate policy allows only MySQL principals originating in the deployment compartment to read the exact leaf certificate OCID, using `target.leaf-certificate.id`. Network association permissions follow Oracle's compartment-scoped MySQL principal requirements. Provisioners still need their own networking, MySQL, certificate-assignment and policy administration permissions; this stack does not create broad administrator grants. Cross-compartment certificates require authority to create the restricted policy in that compartment. See [MySQL mandatory policies](https://docs.oracle.com/en-us/iaas/mysql-database/doc/mandatory-policies-and-permissions.html) and [Certificates policy variables](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/certificatespolicyreference.htm).

OKE Enhanced provides workload identity, but applications must invoke the OCI SDK identity provider and use the exact configured service account. No dynamic group or instance-principal bucket grant is added. The upstream MinIO/S3 client does not consume OKE identity tokens. Native OCI artifact adapters remain separate work; an approved dedicated existing S3 credential group can receive the optional `artifact_s3_group_ocid` policy. This stack creates no IAM users, customer secret keys, credentials or public access tokens. See [OKE workload identity](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contenggrantingworkloadaccesstoresources.htm).

Artifact policies allow inspect/read/create/overwrite objects and bucket read/inspect. Deletion is opt-in; aborting multipart uploads also needs OBJECT_DELETE. Object version deletion, bucket creation/deletion, preauthenticated URL management and replication are excluded. Bucket-name policy matching is case-insensitive, so do not create other buckets differing only by case. Qualify tags, multipart behavior and every workload client; there is no invented OBJECT_UPDATE permission. See [Object Storage permissions](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/objectstoragepolicyreference.htm).

## Costs and teardown

Planning estimates below use 730 hours/month and USD public rates retrieved from Oracle's catalog on 2026-10-01; recheck [Oracle pricing](https://www.oracle.com/cloud/price-list/) before approval. These are estimates, not a budget enforcement mechanism.

| Dev baseline item | Rate / calculation | Monthly estimate |
| --- | --- | --- |
| Enhanced OKE | $0.10 per cluster-hour | $73.00 |
| One E4 node | 2 OCPUs at $0.025/hour + 16 GiB at $0.0015/GiB-hour | $54.02 |
| One 50 GiB balanced boot volume | $0.0255 + $0.017 per GiB-month | $2.13 |
| Optional standalone MySQL.2 | 2 ECPUs at $0.0366/ECPU-hour | $53.44 |
| Optional 100 GiB DB storage | $0.04/GiB-month | $4.00 |
| Base with MySQL | Excludes backup storage and variable services | **about $186.59/month** |

Without MySQL, the baseline is about $129.15/month. Three workers with three boot volumes cost about $168.44/month, plus $73 OKE; HA MySQL adds roughly three instances' compute (~$160.31/month) before its storage/backup charges. Training/Ray/GPU workloads, persistent volumes, LB creation by Helm, artifact versions, requests, logging ingestion/storage, egress, retained backups and externally imported certificates are outside those totals. NAT/service gateways and managed Bastion add no VM compute here; confirm current service/network charges in the estimator. Prod sizing and variable usage can exceed a development budget.

Spin-down `node_count=0` is permitted for dev but leaves OKE, database and storage costs running. Empty buckets can be deleted; nonempty/versioned buckets require deliberate object/version inventory and approved cleanup before OCI will delete them. The provider exposes no `force_destroy` option and this stack implements no blind purge. A reviewed destroy can partially remove other resources before hitting a nonempty bucket, so inventory data first.

Dev database destruction preserves automatic backups and requires a final backup by default. For explicitly disposable dev data, configuration can select `mysql_backup_retention_on_delete=DELETE` and `mysql_final_backup_on_delete=SKIP_FINAL_BACKUP` after data-loss review. Prod requires preservation and enables DB service deletion protection, so teardown needs a separately reviewed protection change. Retained backups, artifact versions, persistent volumes created outside this state and imported certificates can continue billing after cluster teardown; enumerate and retire each deliberately. Terraform state must be retained until cleanup completes.

## Validation and remaining security work

```text
terraform -chdir=terraform init -backend=false -input=false
terraform fmt -check -recursive terraform
terraform -chdir=terraform validate
terraform -chdir=terraform test
```

All six mocked plan tests passed locally. They cover private defaults, optional MySQL/BYOC/Bastion/logging, missing-secret and missing-allowlist rejection, undersized prod rejection, and prod HA baseline. The lock file includes signed package checksums for Windows 386, Windows amd64 and Linux amd64. Tests use `mock_provider`, never cloud credentials or actual OCI calls. Local validation alone cannot prove IAM policy acceptance, quotas, image availability, DB option acceptance, certificate association, node readiness, network access or application compatibility.

Remaining work includes Kubernetes RBAC and restricted service-account use, enforced pod network policies (plain flannel provides no isolation), Pod Security admission, encrypted Kubernetes Secrets/state and appropriate customer-managed keys where required, image signing/scanning and patching, workload TLS/authorization, schema migrations and separate runtime DB users, external secret distribution/rotation, expanded workload identity scope tests, backup restore/failover tests, alerts and cost limits. Worker internet TCP/80 and 443 is broad outbound access for bootstrap; private mirrors and an egress proxy/firewall are needed for destination control. NSGs constrain nodes and services, not application tenants. Logging is optional, and its service logs do not substitute for application or Kubernetes audit observability. No production certification is claimed.
