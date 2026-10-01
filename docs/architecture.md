# Architecture

This repository wraps the upstream Michelangelo chart. It does not fork or replace the platform. The development architecture is a small, private OCI deployment; production deployments require separate qualification.

```mermaid
flowchart TB
  subgraph ops["1 - Operator tooling"]
    direction LR
    browser["Browser<br/>UI 8080 - API 8081"]
    cli["OCI CLI + installer"]
    tf["Terraform<br/>Infrastructure + IAM"]
    helm["Helm + OCI renderer<br/>Pinned upstream release"]
    cli --> tf
    cli --> helm
  end

  subgraph network["2 - Private OCI network"]
    direction TB
    access["OCI Bastion / private route<br/>Operator access"]
    api["Private OKE API<br/>Verified TLS"]
    subgraph cluster["3 - Enhanced OKE"]
      direction TB
      subgraph control["Michelangelo control plane"]
        direction LR
        ui["Studio UI"]
        envoy["Envoy gateway"]
        maapi["API server"]
        controller["Controller"]
        crds["Upstream CRDs"]
        envoy --> maapi
        maapi --> crds
        controller --> crds
      end
      subgraph workflows["Workflow execution"]
        direction LR
        worker["Michelangelo worker"]
        temporal["Temporal<br/>Frontend - History<br/>Matching - System worker"]
        controller --> temporal
        worker --> temporal
      end
      subgraph security["Pod credentials + SQL transport"]
        direction LR
        secrets["Kubernetes Secrets<br/>App password - scoped S3 key<br/>Public CA - Router auth"]
        router["Router sidecars<br/>API + controller pods"]
        probe["Native identity probe<br/>OCI SDK only"]
      end
    end
    mysql[("Managed OCI MySQL<br/>Metadata - workflow - visibility")]
    routing["Service gateway + NAT<br/>OCI services / package egress"]
  end

  subgraph services["4 - OCI managed services"]
    direction LR
    bucket[("Object Storage<br/>Private versioned artifacts")]
    iam["IAM<br/>Scoped bucket policies"]
    certs["Certificates<br/>MySQL server identity"]
    backups[("MySQL backups")]
    logs["Logging - optional<br/>Bucket + subnet logs"]
  end

  browser -->|"Loopback port forwards"| access
  access --> api
  helm --> api
  api --> ui
  api --> envoy
  maapi --> router
  controller --> router
  router -->|"Verified SQL TLS"| mysql
  temporal -->|"Verified SQL TLS"| mysql
  controller -->|"HTTPS S3"| bucket
  worker -->|"HTTPS S3"| bucket
  probe -.->|"Workload identity"| bucket
  secrets -.-> control
  secrets -.-> workflows
  cluster -.-> routing
  routing -.-> bucket
  iam -.-> bucket
  certs -.-> mysql
  mysql --> backups
  bucket -.-> logs
  network -.-> logs
  tf -.-> network
  tf -.-> iam
  tf -.-> bucket

  classDef operator fill:#dbeafe,stroke:#2563eb,color:#172554,stroke-width:2px
  classDef platform fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:2px
  classDef workflow fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:2px
  classDef data fill:#fef3c7,stroke:#d97706,color:#451a03,stroke-width:2px
  classDef identity fill:#ffe4e6,stroke:#e11d48,color:#4c0519,stroke-width:2px
  classDef infra fill:#e2e8f0,stroke:#64748b,color:#0f172a,stroke-width:2px
  class browser,cli,tf,helm operator
  class ui,envoy,maapi,controller,crds platform
  class worker,temporal workflow
  class mysql,bucket,backups data
  class secrets,router,probe,iam,certs identity
  class access,api,routing,logs infra
  style ops fill:#eff6ff,stroke:#93c5fd,color:#172554
  style network fill:#f8fafc,stroke:#94a3b8,color:#0f172a
  style cluster fill:#faf5ff,stroke:#c4b5fd,color:#2e1065
  style control fill:#f5f3ff,stroke:#ddd6fe,color:#2e1065
  style workflows fill:#f0fdf4,stroke:#86efac,color:#14532d
  style security fill:#fff1f2,stroke:#fda4af,color:#4c0519
  style services fill:#fffbeb,stroke:#fcd34d,color:#451a03
```


**Colors:** blue - operator tooling; purple - control plane; green - workflows; amber - data; rose - credentials and identity; slate - networking and optional observability.

The dashed native identity path belongs to the ephemeral OCI SDK diagnostic. It is separate from upstream S3 authentication. Object Storage and other regional OCI services sit outside the VCN; service-gateway/IAM controls govern their access. Upstream S3 clients do not automatically use this identity. The initial artifact path requires a dedicated customer secret key and bucket-scoped IAM policy.

## Components and data flow

The browser downloads Studio from the UI forward on 8080 and calls the separate
Envoy forward on 8081. Envoy translates browser/JSON requests to the API server.
API/controller use Kubernetes CRDs and metadata storage; the controller starts
Temporal workflows, and Michelangelo workers poll Temporal for work. Temporal's
history, matching and system-worker services provide workflow orchestration,
with separate persistence/visibility schemas in managed MySQL.

The controller fetches UniFlow archives and the tested worker storage activity
reads datasets from Object Storage over HTTPS. The example harness subsequently
writes the returned result and registers model metadata through the API.
Database schema initialization and Temporal namespace registration use short-lived
Jobs; the database bootstrap Job temporarily holds administrator credentials and
removes them afterward. Normal workloads reference application Secrets only.

OKE uses private API/worker subnets, a database subnet, an optional Bastion subnet
and a reserved private load-balancer subnet. No load balancer is created in this
MVP. NSGs bound network access; the service gateway routes OCI service traffic,
while NAT supports external image/package downloads. A flannel overlay does not
supply application network isolation. See [infrastructure](infrastructure.md).

Certificates and the service identity/key are prepared outside Terraform. IAM
policies, cluster/network/database/bucket and optional logs are Terraform-managed;
Helm plus the post-renderer owns the platform. [Deployment](deployment.md) and
[access](access.md) describe the operator sequence.

## Integration boundaries

| Layer | OCI choice | Integration boundary |
| --- | --- | --- |
| Kubernetes | OKE | Upstream chart and CRDs; pin chart source and images |
| Metadata | OCI MySQL | Upstream Go MySQL backend; preserve SQL schema |
| Database transport | MySQL Router sidecar | Localhost plaintext inside the pod, verified TLS across the network |
| Workflows | Temporal | Separate persistence and visibility databases; supported TLS configuration |
| Models and artifacts | OCI Object Storage | S3 compatibility endpoint over HTTPS |
| Native API identity | OKE workload identity | OCI SDK clients only; enhanced cluster required |
| Observability | OCI Logging and Kubernetes logs | Enabling Object Storage service logs does not enable all pod logging |
| Administrative access | Private endpoint and Bastion/VPN | No public platform ingress by default |

## Why there is no Ansible layer yet

Terraform manages OCI infrastructure and Helm manages Kubernetes resources. An additional configuration system is unnecessary until a host-level operation cannot be expressed cleanly by those tools. The installer should have one configuration source and deterministic commands.

## Sources

- [Michelangelo overview](https://michelangelo-ai.org/docs/getting-started/overview/)
- [OKE workload identity](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contenggrantingworkloadaccesstoresources.htm)
- [OCI S3 customer secret keys](https://docs.oracle.com/en-us/iaas/Content/Identity/access/working-with-customer-secret-keys.htm)
- [MySQL Router TLS settings](https://dev.mysql.com/doc/mysql-router/8.4/en/mysql-router-conf-options.html)
