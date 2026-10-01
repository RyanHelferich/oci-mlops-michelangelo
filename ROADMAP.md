# Roadmap

## Phase 1: reproducible OCI MVP

- [x] Immutable upstream source/chart/image integration.
- [x] Central configuration and dedicated-compartment resource boundaries.
- [x] Terraform definitions, sizing options and mocked validation.
- [x] Private OKE, managed MySQL, networking and artifact storage modules.
- [x] Private API access helper and operator guide.
- [x] SQL TLS transport, schema bootstrap and Temporal configuration.
- [x] Regression workflow and artifact/model-lineage validation harness.
- [x] Guarded transient shutdown with retained artifact storage.

## Phase 2: OCI native identity and operations

- [x] Provide S3 compatibility and native workload identity allow/deny probes.
- [ ] Expand compatibility coverage to Ray/Spark, serving and every upstream artifact client.
- [ ] Add OCI SDK storage integration through upstream extension points for Go and Python.
- [ ] Remove static S3 credentials from workers, Ray jobs and log collectors.
- [ ] Automate Vault secret delivery with workload identity and documented rotation.
- [ ] Add pod log collection, dashboards, alerts and budget notifications.
- [ ] Validate backup, recovery, upgrades and drift detection.

## Phase 3: Autonomous Database

- [ ] Agree the supported extension contract with upstream maintainers.
- [ ] Port metadata SQL, indexing, schema management and transactions to Oracle SQL.
- [ ] Validate Oracle authentication and TLS/mTLS without modifying upstream core logic.
- [ ] Evaluate workflow persistence independently: replacing Michelangelo metadata does not automatically replace Temporal/Cadence persistence.
- [ ] Benchmark functional parity and publish a migration guide.

## Phase 4: production release

- [ ] Authenticate UI/API access with an OCI-compatible identity provider.
- [ ] Validate authorization and tenant/project isolation.
- [ ] Test control-plane and workflow HA, multiple node pools and autoscaling.
- [ ] Validate CPU/GPU training and serving, regional recovery and upgrades.
- [ ] Publish supported sizing, known limits, operator runbooks and a release qualification report.
