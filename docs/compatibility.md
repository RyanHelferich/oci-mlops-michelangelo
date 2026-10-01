# Compatibility and support boundaries

Inspect this matrix before deploying. Infrastructure creation, successful Helm rendering, healthy services, and a successful ML pipeline are different validation milestones.

| Requirement | Current finding | Release gate |
| --- | --- | --- |
| Preserve upstream code | Chart consumed from an exact source revision | Verify checkout revision and content before rendering |
| OCI Kubernetes | Chart targets Kubernetes >=1.27; Router sidecars require native sidecar support | Qualify the selected OKE version and region |
| OCI MySQL | Go metadata implementation imports `go-sql-driver/mysql` and uses MySQL SQL | Qualify bootstrap, migrations, CRUD and retention on the chosen MySQL version |
| Autonomous Database | No Oracle SQL backend found in the pinned Go storage package | Implement and upstream a backend extension or supported adapter; no endpoint substitution |
| PostgreSQL | Chart exposes a driver option, but API ConfigMap still renders `mysql` and schema is MySQL | Do not advertise chart option as a validated Go backend |
| OCI Object Storage | S3 clients can target OCI's compatibility endpoint | Qualify roundtrip, scope, tags, multipart behavior and workload clients |
| Resource principals for artifacts | Upstream expects S3 access/secret keys | Add native storage adapters across Go, Python, Ray and log collectors, or validate a narrowly scoped credentialless bridge |
| Secure metadata transport | Go DSN has no TLS parameter | Verify cold authentication and negative TLS tests for selected certificates/images |
| Public customer access | Private services are the development default | Authenticated ingress, authorization, TLS and tenant isolation require validation |
| Production scale | Terraform resources can be sized; chart includes fixed singleton deployments | Validate supported replica changes, HA persistence, workflow scale and failover |

## Upstream evidence

Initial inspection used source revision `d717c2b1f8d2512cb6859d5560d54960471dc89f`, chart version `0.11.0`. The authoritative revision used by the installer is recorded in the repository lock file.

- `go/storage/mysql/mysql.go`: MySQL driver, SQL-specific operations, DSN without TLS settings.
- `helm/michelangelo/templates/core/apiserver-configmap.yaml`: MySQL runtime configuration.
- `helm/michelangelo/templates/core/controllermgr-configmap.yaml`: controller MySQL configuration.
- `helm/michelangelo/files/schema/mysql-init-schema.sql`: hardcoded `michelangelo` database.
- `helm/michelangelo/values.yaml`: S3 credential references and workflow backend configuration.

## MVP exceptions

The wrapper uses OCI MySQL for compatibility; Autonomous Database integration is on the roadmap. The upstream S3 API requires static customer secret keys; this must be presented as an explicit compatibility exception, stored outside version control and scoped to the artifact bucket. IAM policies for workload identity do not convert S3 clients into OCI SDK clients.

There is no blanket security or production certification. Check [validation guide](validation.md) for what has actually passed.
