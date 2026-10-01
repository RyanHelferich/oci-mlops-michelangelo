# Security model

## Identities

The operator authenticates with an OCI CLI profile to provision infrastructure. Never copy the OCI private key or configuration into this repository, a container image, or a Kubernetes Secret.

OKE-aware application code should use workload identity. Scope policies to the exact cluster OCID, namespace, service account and target resource. An enhanced cluster is required. The upstream artifact clients use S3 credentials, so their IAM policy must instead scope a dedicated non-administrator identity to the exact artifact bucket. Do not reuse the operator's customer secret key.

Database credentials remain necessary for upstream MySQL and workflow persistence. Use separate database accounts for Michelangelo, Temporal persistence, visibility and bootstrap operations. Bootstrap permissions should be removed or isolated after schema creation. The upstream API performs schema initialization on startup, so disabling that behavior requires a reviewed wrapper change.

The MVP uses one schema-scoped application user for the three metadata/workflow
schemas and a separate provisioning administrator. The administrator is supplied
only to a temporary bootstrap Job, whose Secret is removed afterward. Separate
workflow and metadata identities remain a production hardening requirement.

The pinned controller requires cluster-wide watches despite the chart's namespace
option. Deploy into a dedicated OKE cluster. Runtime CRD mutation and namespace
deletion are removed from its role; Kubernetes orchestration access still spans
the dedicated cluster. This does not support hostile multi-tenant workloads.

## Network and transport

Private Kubernetes API, private worker nodes and private database endpoints are the default architecture. Permit database port 3306 only from the relevant worker/pod network. Platform Services should remain ClusterIP until authentication and authorization are validated.

The MySQL Router wrapper is intended to bind to loopback in each database client pod. It requires verified TLS to the remote database. Plaintext must never traverse the VCN. Never turn off `require_secure_transport` to accommodate the upstream Go DSN. Mount a trusted CA and use a server name matching the database certificate.

Object Storage access must use HTTPS. A private bucket is not equivalent to a private network endpoint; apply IAM and routing restrictions separately. NAT access permits image downloads and external dependencies and should be accounted for in egress policy.

## Secrets and state

- Reference existing Kubernetes Secrets in Helm values; secret values must not appear in committed configuration.
- Do not supply passwords with command-line flags. Prefer a secret manager or process environment, and redact subprocess diagnostics.
- Terraform state and saved plans can contain database credentials even when outputs are marked sensitive. Keep local state private; production deployments need a reviewed remote state and locking design.
- Versioned artifact buckets can retain deleted object versions. Cleanup must be an explicit operator action after reviewing retention requirements.
- Kubernetes Secrets require careful RBAC and encryption configuration; base64 is not encryption.

## Remaining security gates

Validate database identity verification, image digests, non-root container support, Pod Security admission, network policies, secret rotation, upstream cluster-wide RBAC, authentication at the UI/API boundary and authorization for project resources. Upstream controllers need broad Kubernetes access for dynamic ML workloads; this is not a tenant isolation guarantee.

Object Storage service logging, pod logging, workflow logging and model prediction logging are separate configurations. Logs may contain training data, parameters or credentials; retention and access controls need workload-specific review.

See [compatibility](compatibility.md) and [validation](validation.md) for the current gates.

## Operator access

Use [the private access guide](access.md) for Bastion and loopback forwards.
Both browser ports bind to 127.0.0.1. This is an OCI/Kubernetes-authorized operator
path; it does not establish application SSO, customer authorization or public
end-user access. [Deployment](deployment.md) describes credential/bootstrap order.
