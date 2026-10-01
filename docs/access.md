# Private operator access

Deploy and install the platform before establishing operator access. There is
no default public ingress. An authenticated SSH tunnel through OCI Bastion reaches
the private OKE API, and Kubernetes forwards the UI/API to loopback ports.
OCI/Kubernetes permissions control this path; application SSO and production
end-user authorization require separate configuration.

## 1. Permit your operator connection

Before infrastructure planning, set `terraform.enable_bastion=true` and
`terraform.bastion_client_cidrs` to your current public IPv4 address as `/32`.
The rule uses your public NAT address, not your laptop's private Wi-Fi address.
Do not use `0.0.0.0/0`. Operator IP changes require a reviewed infrastructure update.
An existing private VPN route is an alternative; do not create a Bastion session
when using a different independently verified API route.

## 2. Create the private API tunnel

From the repository root with the configured OCI profile and deployed Terraform
state, enable local mutations for this authorized session and run:

```text
python tools/connect_bastion.py --create-session
```

The helper checks cluster/compartment identity, creates a one-hour TCP-forwarding
session and a unique local SSH key, and writes isolated files under
`.local/bastion`. It prints the exact SSH command, kubeconfig path and context.
Run that SSH command in a separate terminal and leave it running. Retain host-key
verification; do not disable Kubernetes CA verification to fix a tunnel issue.

The local API is `https://127.0.0.1:16443`; kubeconfig uses the private endpoint's
TLS server name so the original cluster certificate remains verified. This is
an API tunnel, not a database tunnel or public platform endpoint.

Set `KUBECONFIG` in **each** terminal using the installer or bootstrap helpers:

```powershell
# Windows PowerShell
$env:KUBECONFIG = (Resolve-Path .local/bastion/kubeconfig).Path
```

```bash
# Linux/macOS, from the repository root
export KUBECONFIG="$PWD/.local/bastion/kubeconfig"
```

Copy the helper's exact context and API URL into `deployment.kube_context` and
`deployment.kube_api_server` before rendering/installing. Then check:

```text
kubectl --kubeconfig .local/bastion/kubeconfig config current-context
kubectl --kubeconfig .local/bastion/kubeconfig -n michelangelo get pods
kubectl --kubeconfig .local/bastion/kubeconfig -n michelangelo get services
```

Use your configured namespace if it differs. The helper does not alter your
global kubeconfig. Keep its generated keys and session metadata out of Git.

## 3. Forward both browser endpoints

After successful installation, open two more terminals and run one command in
each. These service names match the example release `michelangelo`:

```text
kubectl --kubeconfig .local/bastion/kubeconfig -n michelangelo port-forward --address 127.0.0.1 service/michelangelo-ui 8080:80
```

```text
kubectl --kubeconfig .local/bastion/kubeconfig -n michelangelo port-forward --address 127.0.0.1 service/michelangelo-envoy 8081:8081
```

Open **http://127.0.0.1:8080**. The UI's configured API base URL must be
**http://127.0.0.1:8081**. Keep the SSH session and both port forwards running.
The allowed CORS origins are localhost/127.0.0.1 on port 8080. Do not arbitrarily
change browser ports without updating and qualifying the wrapper configuration.

The UI Service exposes port 80 but its non-root container listens on 8080; forward
the **Service** port as shown. Envoy's Service exposes 8081. For a different
release, use `<release>-ui` and `<release>-envoy`. The private Kubernetes API's
16443 is not a browser URL. Temporal web is disabled; the pipeline example uses
its own temporary frontend port forward for workflow diagnostics.

## 4. Use the platform and end the session

Inspect resources in the Studio UI and run
[the small workflow example](../examples/mvp-pipeline/README.md) for the tested
creation/training/model-registration path. Ray/Spark and inference serving remain
unqualified. Do not use real sensitive datasets for this development qualification.

Press Ctrl+C in the two forward terminals and SSH terminal when finished. Ending
a tunnel does **not** shut down OCI resources or stop billing. Use
[development shutdown](operations.md#teardown) separately when appropriate.
Bastion sessions expire after one hour; create a new session/key and restart
forwards when needed. A rebuild requires freshly generated kubeconfig and outputs.

## Troubleshooting

| Symptom | Check / action |
| --- | --- |
| Connection refused on 16443 | SSH command is running, session is active, and local port is free |
| SSH fails | Current public IP matches `/32`, correct helper-generated key/session, and outbound TCP/22 is allowed by the operator network |
| Kubernetes certificate error | Correct isolated kubeconfig, original CA and private TLS server name; never add insecure-skip-tls-verify |
| kubectl unauthorized/forbidden | OCI profile/token and Kubernetes permissions for this cluster |
| UI loads but all API calls fail | Second Envoy forward on 8081, matching UI apiBaseUrl, browser origin on port 8080 |
| Service not found | Namespace/release names match; Helm install completed |
| Forward exits after pod rollout | Restart that forward against the Service after rollout completes |
| Local port occupied | Stop only your conflicting process; preserve the documented UI/API port contract |
| Previously working connection stops | Bastion expiry, changed operator IP, expired OCI credentials, or deleted/rebuilt cluster |

Check access configuration against the pinned chart/helper and qualify browser
behavior in the target environment; see [validation](validation.md).
