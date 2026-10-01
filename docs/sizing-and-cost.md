# Sizing and development cost

## Initial MVP target

| Resource | Development target | Reason |
| --- | --- | --- |
| OKE | One enhanced cluster | Private Kubernetes API and support for workload identity |
| Worker pool | One VM.Standard.E4.Flex node, 2 OCPUs, 16 GiB | Small x86 control plane and a lightweight test pipeline |
| Node boot volume | 50 GiB, balanced performance | Keep the default disk footprint small |
| Database | MySQL.2, 2 ECPUs, 16 GiB, standalone | Small available managed database shape; no analytical HeatWave cluster |
| Database storage | 100 GiB initial target | Small service baseline; confirm the API's allowed minimum |
| Artifact bucket | Private, Standard tier | Small datasets and models; lifecycle retention configurable |
| Access | OCI Bastion or existing VPN | Access private endpoints without a dedicated access VM |
| Training | One small CPU workflow | No GPUs and no large distributed training cluster by default |

This is a development sizing baseline, not a throughput guarantee or minimum
for arbitrary workloads. Kubernetes, Temporal and image pulls consume worker
resources; measure workload concurrency and scheduling before scaling.

## USD estimate

Oracle's public pricing catalog queried on 2026-10-01 returned the following list rates. Discounts, free allocations, taxes, workload usage and service changes can alter actual charges.

| Item | Rate | 730-hour estimate |
| --- | --- | --- |
| Enhanced OKE | $0.10 / cluster-hour | $73.00 |
| Two E4 OCPUs | $0.025 / OCPU-hour | $36.50 |
| 16 GiB E4 memory | $0.0015 / GiB-hour | $17.52 |
| Two MySQL ECPUs | $0.0366 / ECPU-hour | $53.44 |
| 100 GiB MySQL storage | $0.04 / GiB-month | $4.00 |
| 50 GiB balanced boot volume | $0.0255 storage + 10 × $0.0017 performance units / GiB-month | $2.13 |
| Subtotal | | **$186.59/month** |

Backups, Object Storage, logs, extra training nodes and networking usage are additional. MySQL backup storage list rate is $0.04/GiB-month. Object Storage's catalog includes a free tier and a paid rate of $0.0255/GiB-month; don't assume the free allocation remains available in a shared tenancy. Set a customer-specific budget and allow headroom for variable usage.

For 40 hours of active development, the four hourly compute/control-plane items above total approximately $9.89, plus retained storage and usage. Stopping workers does not remove the OKE enhanced-cluster charge. Stopping MySQL does not remove storage and backup charges. The most complete cost reduction is reviewed teardown of disposable infrastructure after preserving required data.

An OCI Budget alert is a notification, not a hard spending cap. Track actual compartment cost and always record which resources remain after a session.

Sources: [Oracle price list](https://www.oracle.com/cloud/price-list/), [OKE pricing](https://www.oracle.com/dk/cloud/cloud-native/kubernetes-engine/pricing/), [pricing catalog](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/), [supported MySQL shapes](https://docs.oracle.com/en-us/iaas/mysql-database/doc/supported-shapes.html).

## Production sizing

Start with measured training concurrency, dataset size, model size, API throughput and recovery targets. Production commonly needs multiple nodes across fault domains, HA MySQL, more workflow replicas, separate training/serving pools and controlled autoscaling. Those changes exceed the development cost estimate and need a separate reviewed plan.

The upstream chart contains fixed singleton deployments for some services. Increasing infrastructure capacity alone does not make those services highly available. Validate chart post-rendering or supported upstream scaling configuration before documenting replica changes as supported.
