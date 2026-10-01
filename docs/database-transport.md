# Database transport

The Helm post-renderer in `security/mysql-router/` adds a MySQL Router native init sidecar to the Michelangelo apiserver and controller manager. Upstream chart source stays unchanged. Both workloads keep the upstream `metadataStorage.host=127.0.0.1` and `metadataStorage.port=6446`; Router connects to the private database DNS name over verified TLS. Temporal keeps its own native SQL TLS configuration and receives no Router injection.

The deployment baseline is Michelangelo release `v0.11.0`, commit `37852aace4dd9f9658c11c19c56d19692ac5d2c4`. Transform tests also cover the originally inspected main commit `d717c2b1f8d2512cb6859d5560d54960471dc89f`. The relevant upstream sources are `helm/michelangelo/templates/core/{apiserver,controllermgr}-deployment.yaml` and their component ConfigMaps. The runtime Go client uses `go-sql-driver/mysql v1.9.3` with a DSN that has no TLS parameters.

## TLS and authentication

Router has exactly one static `classic` route:

```ini
[routing:metadata]
bind_address = 127.0.0.1
bind_port = 6446
destinations = <certificate-matching-private-database-DNS>:3306
routing_strategy = first-available
protocol = classic
client_ssl_mode = PREFERRED
client_ssl_cert = /router/auth/tls.crt
client_ssl_key = /router/auth/tls.key
server_ssl_mode = REQUIRED
server_ssl_verify = VERIFY_IDENTITY
server_ssl_ca = /router/ca/ca.pem
```

`REQUIRED` encrypts Router-to-database connections; `VERIFY_IDENTITY` verifies trust and the destination identity using the configured CA. `PREFERRED` accepts plaintext or TLS on the loopback frontend. These are independent settings in the [official Router 8.4 configuration reference](https://dev.mysql.com/doc/mysql-router/8.4/en/mysql-router-conf-options.html) and [TLS configuration guide](https://dev.mysql.com/doc/mysql-router/8.4/en/mysql-router-configuration-tls.html).

Runtime testing corrected the initial `client_ssl_mode=DISABLED` design after a real container test found that it rejects a cold `caching_sha2_password` login. A direct TLS login warmed the server authentication cache and made that broken design appear to work until the cache was reset. Router's [official 8.4.6 authentication implementation](https://github.com/mysql/mysql-server/blob/mysql-8.4.6/router/src/routing/src/classic_auth_caching_sha2_forwarder.cc) explains that DISABLED has no frontend SSL context to decrypt the client's RSA password and forwards a public-key request that the encrypted server connection treats as an invalid password. `PREFERRED` with a dedicated Router RSA key fixes this without changing the upstream Go DSN or weakening backend TLS. Do not warm caches as a deployment workaround or enable a deprecated authentication plugin.

Three existing Secrets belong in the workload namespace:

| Secret | Required data | Purpose |
| --- | --- | --- |
| Upstream metadata Secret | `password` | Application and schema credentials; Router never receives this Secret |
| Database CA Secret | Configured key, default `ca.pem` | Trusted database CA chain in PEM format; public certificates only |
| Dedicated Router auth Secret | `tls.crt`, `tls.key` | RSA certificate and matching private key for frontend authentication |

Use a dedicated Router RSA key of at least 2048 bits with a matching PEM X.509 certificate. A self-signed certificate is sufficient for the upstream plaintext loopback client; any client that explicitly verifies frontend TLS needs trust configured for this Router identity. Never reuse the database server private key. The renderer requires the Router auth Secret and CA Secret to have different names. Do not commit private keys, passwords, or live Secret manifests.

The database hostname must match its server certificate identity and resolve privately. Supply the CA chain from the database's trusted provisioning process. An arbitrary DNS alias or IP-to-DNS conversion does not establish certificate identity. A missing CA, wrong CA, wrong identity, or database without TLS must fail; there is no remote plaintext fallback.

## Installer contract

Install `security/mysql-router/requirements.txt` (`PyYAML==6.0.3`) into the installer's isolated Python environment. Do not replace the OCI CLI's Python dependencies, which have a different PyYAML constraint. The installer uses `--post-renderer <python-interpreter> --post-renderer-args <absolute-path-to-post-renderer.py>`, tested through Helm on Windows. The optional `post-renderer.sh` wrapper delegates to `post-renderer.py`; `MYSQL_ROUTER_PYTHON` selects its interpreter. On a POSIX checkout, ensure that wrapper has its executable bit before supplying it directly to `helm --post-renderer`. Input and output are UTF-8 YAML on stdin/stdout regardless of Windows terminal encoding; errors go to stderr with nonzero status and no partial manifest output.

| Environment variable | Value |
| --- | --- |
| `MYSQL_ROUTER_IMAGE` | Required stock `container-registry.oracle.com/mysql/community-router` reference with an immutable SHA-256 digest |
| `MYSQL_ROUTER_HOST` | Required remote DNS hostname that matches the database certificate |
| `MYSQL_ROUTER_PORT` | Remote MySQL port, default `3306` |
| `MYSQL_ROUTER_CA_SECRET` | Required existing Secret containing the database CA chain |
| `MYSQL_ROUTER_CA_KEY` | CA Secret data key, default `ca.pem` |
| `MYSQL_ROUTER_AUTH_SECRET` | Required separate existing RSA `tls.crt`/`tls.key` Secret |
| `MYSQL_ROUTER_REQUIRE_TARGET` | Default `1`: reject streams with no eligible workloads; `0` is only for intentionally unrelated renders |
| `MYSQL_ROUTER_PYTHON` | Interpreter path for the shell wrapper, default `python3` |

The verified amd64 stock image is:

```text
container-registry.oracle.com/mysql/community-router:8.4.6@sha256:468ea2e12477d6c1d8256f575aac33f09a3281531ef982f13bfeea04b1ba02de
```

This digest is an amd64 manifest, not a multiarchitecture guarantee. Configure compatible node placement and verify any replacement image's architecture and runtime behavior. Kubernetes `command: [mysqlrouter]` plus `args: [--config, /router/config/mysqlrouter.conf]` overrides the stock image bootstrap entrypoint. No InnoDB Cluster bootstrap, metadata-cache plugin, separate Router database user, or application password in Router configuration is needed.

## Transform and lifecycle safeguards

Only `apps/v1` Deployments labelled `app.kubernetes.io/part-of=michelangelo` with component `apiserver` or `controllermgr` qualify. The renderer verifies the expected main container, its `/config` mount, the referenced ConfigMap, the component's embedded `base.yaml`, and its MySQL endpoint. Apiserver's `wait-for-metadata-storage` and `schema-init` must also use the loopback endpoint and MySQL commands. A remote or inconsistent endpoint fails the entire render. `hostNetwork` is rejected so the listener cannot become host-wide loopback access.

Router is first in `initContainers`, with `restartPolicy: Always`. Its startup probe executes Bash's TCP check inside the container against `127.0.0.1:6446`; a kubelet TCP probe would originate outside the Pod network and cannot reach this listener. The native sidecar startup probe must succeed before Kubernetes starts the next init container. It verifies listener startup, while the upstream wait container verifies database reachability. Native sidecars are stable in Kubernetes 1.33; earlier clusters require the feature enabled, defaulting on since 1.29. See the [Kubernetes sidecar lifecycle documentation](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/).

Router runs as UID `65534`, with a nonzero Pod `fsGroup`, dropped capabilities, no privilege escalation, `RuntimeDefault` seccomp, and a read-only root filesystem. Configuration and CA mounts are read-only; the RSA key mount uses `0440` permissions and Pod group access. Writable state and `/tmp` use separate `16Mi` emptyDir volumes. Requests are `50m` CPU and `64Mi` memory; limits are `250m` CPU and `128Mi` memory. The chart's existing Pod group is preserved.

The renderer produces a namespaced Router ConfigMap per workload and a Pod configuration checksum to trigger rollouts when the route changes. It rejects malformed YAML, duplicate mapping keys/resources, conflicting Router sidecars, altered managed volumes, and conflicting ConfigMaps, including injected extra plaintext routes. Reapplying identical output is idempotent. Secret rotation still requires an explicit workload rollout because Secret contents are not available to the renderer and Router loads certificates at startup.

## Verification and limits

The transform suite checks strict manifest parsing and sidecar lifecycle behavior.
The Docker probe checks cold SHA2 authentication, backend TLS, incorrect
CA/hostname rejection, missing server TLS, listener isolation and the exact
upstream Go driver/DSN. Review its generated local evidence before recording
`router_tls_verified` in configuration. Do not publish environment-specific logs.

Set `MYSQL_ROUTER_TEST_HELM` to a Helm executable and `MYSQL_ROUTER_TEST_UPSTREAM` to the source checkout to include both real chart revision tests. Those tests read tracked chart files with `git archive` into temporary directories and omit disabled chart dependency declarations there to render the core templates offline. They never edit the source checkout. Without these variables, two chart tests are explicitly skipped. Temporal selection is checked using an unrelated workload with native SQL TLS settings.

The local Docker TLS probe needs `requirements-runtime-test.txt` and Docker; a Go compiler enables the additional exact upstream driver/DSN test:

```sh
python security/mysql-router/test_runtime_tls.py
```

The probe uses pinned Router and MySQL images, generates disposable distinct database and Router certificates, publishes no ports, and cleans up only its uniquely named containers/network/anonymous database volumes. `security/mysql-router/runtime-evidence.json` records actual image references, version, observed backend cipher, authentication checks, and TLS rejection results. This local test does not validate an OCI database, OKE scheduling, Kubernetes Secret permissions, network policy enforcement, or a complete apiserver startup; see [validation guide](validation.md) for environment qualification. Production isolation and recovery remain separate release gates.
