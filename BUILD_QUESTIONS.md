# Design decisions and open questions

| Area | Current design / remaining decision |
| --- | --- |
| Database | OCI MySQL compatibility backend; Autonomous integration requires an upstream-supported Oracle SQL extension |
| Regions and sizing | Configurable dedicated customer compartment; supported region defaults and dev shapes are documented in configuration |
| SQL transport | Loopback Router with remote CA/hostname verification; qualify cold authentication and negative TLS checks |
| Artifact identity | Upstream S3 clients require a dedicated scoped key; native workload adapters remain on the roadmap |
| Application access | Choose identity provider, authorization mapping and authenticated ingress before public end-user access |
| HA and recovery | Define concurrency, workload size, GPU needs, RPO/RTO and retention for each production deployment |
| Storage compatibility | Qualify object metadata/tags, multipart behavior and every workload client |

Keep customer/account-specific decisions, credentials and diagnostic records in
protected storage outside the public repository.
