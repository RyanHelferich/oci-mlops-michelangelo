#!/usr/bin/env python3
"""Local, fail-closed OCI/Michelangelo wrapper. Python standard library only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "37852aace4dd9f9658c11c19c56d19692ac5d2c4"
REPOSITORY = "https://github.com/michelangelo-ai/michelangelo.git"
CHART_VERSION = "0.11.0"
DEPENDENCY_SHA256 = {"cadence-1.1.0.tgz": "9e3855135bdcc5103aa64393cfba0d9f35fdef68608b249d8471ff006bca1a21",
                     "temporal-0.44.0.tgz": "2ce7e7ff78888cb7732e9b4e469e60da7591c02d81d3149c9a8d1ad21d3a8a03"}
SERVICE_DIGESTS = {
    "apiserver": "27f2a509371a6b6848a3b3be78558d4042f8c9b488c7927f5dff92b97a860f12",
    "worker": "a1b500772600e67c7dd02b1e64555ab969f56e14106f27b5236f9719455a1e5f",
    "ui": "d74444d4690e7bf66664be5f70385bd23c739a0e18943e7ddbb96a6bb26e73b4",
    "controllermgr": "ba9e8b77f3c805a61b99d57274a2cec2d4606d4b04f4e6d4a9397a3c0fa23a22",
}
TF_TYPES = {
    **dict.fromkeys(("tenancy_ocid", "compartment_ocid", "region", "config_file_profile", "environment", "project_name", "kubernetes_version", "node_image_ocid", "node_shape", "vcn_cidr", "artifact_bucket_name", "workload_namespace", "database_mode", "mysql_admin_username", "mysql_shape"), str),
    **dict.fromkeys(("node_ocpus", "node_memory_gbs", "node_boot_volume_gbs", "mysql_storage_gbs"), (int, float)),
    **dict.fromkeys(("availability_domains", "admin_cidrs", "workload_service_accounts", "bastion_client_cidrs"), list),
    **dict.fromkeys(("enable_nat_gateway", "enable_logging", "enable_bastion", "mysql_high_availability"), bool),
    "node_count": (int, type(None)),
}
TF_OPTIONAL_TYPES = {"artifact_allow_delete": bool, "log_retention_days": int, "tags": dict,
                     "mysql_certificate_ocid": (str, type(None)), "mysql_certificate_compartment_ocid": (str, type(None)),
                     "mysql_hostname_label": str, "artifact_s3_group_ocid": (str, type(None)), "mysql_backup_retention_days": int,
                     "mysql_backup_retention_on_delete": str, "mysql_final_backup_on_delete": str}
SENSITIVE_KEYS = {"password", "rootPassword", "mysql_admin_password", "accessKeyId", "secretAccessKey", "awsAccessKeyId", "awsSecretAccessKey", "private_key", "token"}
DNS_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
DIGEST_IMAGE = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}\Z")


class InstallerError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise InstallerError(message)


def run(argv, *, cwd=None, env=None, input_text=None, input_bytes=None):
    """Never echo subprocess output: tools can include credentials in diagnostics."""
    argv = [str(x) for x in argv]
    if argv and argv[0] == "helm" and shutil.which("helm") is None:
        local_helm = ROOT / ".local/bin/helm/windows-amd64/helm.exe"
        if local_helm.is_file():
            argv[0] = str(local_helm)
    require(bool(argv) and shutil.which(str(argv[0])) is not None, f"Required tool unavailable: {Path(str(argv[0])).name}")
    try:
        result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                                input=input_bytes if input_bytes is not None else input_text,
                                capture_output=True, text=input_bytes is None, timeout=1800,
                                encoding="utf-8" if input_bytes is None else None,
                                errors="replace" if input_bytes is None else None,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallerError(f"{Path(str(argv[0])).name} did not complete; inspect locally without publishing credentials") from exc
    require(result.returncode == 0, f"{Path(str(argv[0])).name} failed (exit {result.returncode}); output withheld because it may contain secrets")
    return result.stdout


def read_json(path):
    try:
        def unique(pairs):
            obj = {}
            for key, value in pairs:
                require(key not in obj, "Duplicate JSON key rejected")
                obj[key] = value
            return obj
        return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(InstallerError("Non-finite JSON number rejected")))
    except (OSError, ValueError) as exc:
        raise InstallerError("Cannot read configuration JSON; copy config/config.example.json to config/config.json") from exc


def keys(obj, required, optional=(), where="config"):
    require(type(obj) is dict, f"{where} must be an object")
    require(set(required) <= set(obj), f"{where} missing keys: {', '.join(sorted(set(required) - set(obj)))}")
    require(set(obj) <= set(required) | set(optional), f"{where} has unsupported keys")


def typed(obj, key, typ, where):
    value = obj[key]
    require(isinstance(value, typ) and (typ is bool or type(value) is not bool), f"{where}.{key} has invalid type")
    if typ is str:
        require(bool(value.strip()) and not any(c in value for c in "\r\n\x00"), f"{where}.{key} must be a nonempty single-line string")
    return value


def reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", key.lower())
            require(normalized not in {re.sub(r"[^a-z]", "", k.lower()) for k in SENSITIVE_KEYS}, "Inline credential field rejected; use existing Kubernetes Secrets and TF_VAR_mysql_admin_password")
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)


def validate(config):
    keys(config, ("schema_version", "terraform", "platform", "deployment"))
    require(type(config["schema_version"]) is int and config["schema_version"] == 1, "Unsupported schema_version")
    reject_secrets(config)
    tf = config["terraform"]
    keys(tf, tuple(TF_TYPES), tuple(TF_OPTIONAL_TYPES), where="terraform")
    for key, typ in {**TF_TYPES, **TF_OPTIONAL_TYPES}.items():
        if key not in tf:
            continue
        typed(tf, key, typ, "terraform")
        if typ is list:
            require(all(type(x) is str and x.strip() for x in tf[key]), f"terraform.{key} requires nonempty strings")
    require(tf["database_mode"] in ("none", "mysql"), "Autonomous and other database modes are unsupported")
    require(tf["environment"] in ("dev", "prod"), "terraform.environment must be dev or prod")
    require(tf["region"] in ("us-ashburn-1", "us-phoenix-1"), "MVP supports Ashburn or Phoenix")
    require(tf["node_count"] is None or tf["node_count"] >= 1, "node_count must be null or positive")
    for key in ("node_ocpus", "node_memory_gbs", "node_boot_volume_gbs", "mysql_storage_gbs"):
        require(tf[key] > 0, f"terraform.{key} must be positive")
    if tf["environment"] == "dev":
        require(tf["node_ocpus"] <= 2 and tf["node_memory_gbs"] <= 16 and (tf["node_count"] is None or tf["node_count"] <= 3), "Dev node size/count exceeds 2 OCPU / 16 GiB / 3 nodes guardrail")
    p = config["platform"]
    keys(p, ("metadataStorage", "objectStorage", "workflow", "mysqlRouter", "ui", "images"), where="platform")
    db = p["metadataStorage"]
    keys(db, ("driver", "host", "port", "database", "user", "existingSecret"), where="platform.metadataStorage")
    for key in ("driver", "host", "database", "user", "existingSecret"):
        typed(db, key, str, "platform.metadataStorage")
    typed(db, "port", int, "platform.metadataStorage")
    require(db["driver"] == "mysql", "Pinned Go metadata runtime supports MySQL only; Postgres/Autonomous unsupported")
    require(db["database"] == "michelangelo", "Upstream schema hardcodes database michelangelo")
    require(db["host"] == "127.0.0.1" and db["port"] == 6446, "MySQL must use the local TLS Router at 127.0.0.1:6446")
    storage = p["objectStorage"]
    keys(storage, ("auth", "endpoint", "secure", "region", "bucket", "existingSecret"), where="platform.objectStorage")
    for key in ("auth", "endpoint", "region", "bucket", "existingSecret"):
        typed(storage, key, str, "platform.objectStorage")
    typed(storage, "secure", bool, "platform.objectStorage")
    require(storage["auth"] == "s3_static", "Direct OCI resource principal authentication is unsupported; use dedicated S3 customer secret key")
    require(storage["secure"] is True, "Object storage TLS is required")
    require(re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", storage["endpoint"]) is not None, "Object endpoint must be a hostname, without URL scheme or credentials")
    require(storage["region"] == tf["region"], "Object storage and Terraform region must match")
    require(storage["bucket"] == tf["artifact_bucket_name"], "Object storage bucket must match Terraform input")
    workflow = p["workflow"]
    keys(workflow, ("engine", "mode", "endpoint", "domain"), where="platform.workflow")
    for key in workflow:
        typed(workflow, key, str, "platform.workflow")
    require(workflow["engine"] == "temporal", "This wrapper supports Temporal only")
    require(workflow["mode"] in ("bundled", "external"), "Workflow mode must be bundled or external")
    require(re.fullmatch(r"[A-Za-z0-9.-]+:[0-9]+", workflow["endpoint"]) is not None, "Temporal endpoint must be host:port")
    require(DNS_NAME.fullmatch(workflow["domain"]) is not None, "Temporal domain must be a DNS label (used by upstream shell job)")
    router = p["mysqlRouter"]
    keys(router, ("image", "host", "port", "caSecret", "caKey", "authSecret", "python"), where="platform.mysqlRouter")
    for key in ("image", "host", "caSecret", "caKey", "authSecret", "python"):
        typed(router, key, str, "platform.mysqlRouter")
    typed(router, "port", int, "platform.mysqlRouter")
    require(DIGEST_IMAGE.fullmatch(router["image"]) is not None, "MySQL Router image must use an immutable SHA256 digest")
    require(re.fullmatch(r"[A-Za-z0-9.-]+", router["host"]) is not None and router["host"] not in ("localhost", "127.0.0.1"), "Router backend must be remote MySQL DNS name")
    require(1 <= router["port"] <= 65535, "Router port out of range")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+", router["caKey"]) is not None, "Router CA key must be a plain filename")
    require(router["caSecret"] != router["authSecret"], "Router authentication certificate Secret must be separate from the DB CA Secret")
    keys(p["ui"], ("apiBaseUrl",), where="platform.ui")
    typed(p["ui"], "apiBaseUrl", str, "platform.ui")
    require(p["ui"]["apiBaseUrl"] in ("http://127.0.0.1:8081", "http://localhost:8081"), "MVP UI access uses a local port-forward only")
    keys(p["images"], tuple(SERVICE_DIGESTS), where="platform.images")
    for service, digest in SERVICE_DIGESTS.items():
        require(p["images"][service] == f"ghcr.io/michelangelo-ai/{service}:0.11.0@sha256:{digest}", f"{service} image must match the verified 0.11.0 digest")
    d = config["deployment"]
    keys(d, ("upstream_path", "namespace", "release", "kube_context", "kube_api_server", "mysql_mvp_approved", "static_object_storage_credentials_approved", "enable_mutations", "workflow_namespace_registered", "router_tls_verified", "timeout_seconds"), where="deployment")
    for key in ("upstream_path", "namespace", "release", "kube_context", "kube_api_server"):
        typed(d, key, str, "deployment")
    for key in ("mysql_mvp_approved", "static_object_storage_credentials_approved", "enable_mutations", "workflow_namespace_registered", "router_tls_verified"):
        typed(d, key, bool, "deployment")
    typed(d, "timeout_seconds", int, "deployment")
    require(60 <= d["timeout_seconds"] <= 1800, "timeout_seconds must be 60..1800")
    require(d["kube_api_server"].startswith("https://"), "Kubernetes API must use HTTPS")
    require(d["namespace"] == tf["workload_namespace"], "Namespace must match Terraform workload_namespace")
    if workflow["mode"] == "bundled":
        require(workflow["endpoint"] == d["release"] + "-temporal-frontend:7233", "Bundled workflow endpoint must match release-temporal-frontend:7233")
    for name in (d["namespace"], d["release"], db["existingSecret"], storage["existingSecret"], router["caSecret"], router["authSecret"]):
        require(DNS_NAME.fullmatch(name) is not None, "Kubernetes names must be DNS labels of at most 63 characters")
    return config


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_digest(path):
    entries = [(p.relative_to(path).as_posix(), file_digest(p)) for p in sorted(Path(path).rglob("*")) if p.is_file() and not p.is_symlink() and ".terraform" not in p.parts and "__pycache__" not in p.parts and p.suffix in (".tf", ".hcl", ".json", ".yaml", ".yml", ".tpl", ".py", ".sh", ".sql", ".tgz")]
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def source_path(config, root=ROOT):
    return (root / config["deployment"]["upstream_path"]).resolve()


def verify_source(source):
    """Compare every working file with commit blobs, independent of a damaged index."""
    require(source.is_dir(), "Pinned source missing; run fetch")
    actual = run(["git", "-C", source, "rev-parse", "HEAD"]).strip()
    require(actual == REVISION, "Upstream HEAD does not match the exact source pin")
    listing = run(["git", "-C", source, "ls-tree", "-r", REVISION]).splitlines()
    paths, expected = [], []
    for line in listing:
        info, name = line.split("\t", 1)
        mode, kind, digest = info.split()
        require(kind == "blob" and mode in ("100644", "100755", "120000"), "Upstream submodules are unsupported")
        if mode == "120000":
            link = source / name
            # Windows Git often materializes symlink blobs as ordinary text files.
            data = os.readlink(link).encode() if link.is_symlink() else link.read_bytes()
            actual_link_hash = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            require(actual_link_hash == digest, "Upstream symlink differs from pinned commit")
            continue
        require((source / name).is_file() and not (source / name).is_symlink(), "Upstream tracked file missing or symlinked")
        paths.append(name)
        expected.append(digest)
    # Git clean conversion normalizes checkout CRLF consistently with the pinned blobs.
    observed = run(["git", "hash-object", "--stdin-paths"], cwd=source, input_text="\n".join(paths) + "\n").splitlines()
    require(len(observed) == len(expected), "Upstream hash verification incomplete")
    mismatches = [(p, wanted) for p, wanted, found in zip(paths, expected, observed) if wanted != found]
    if mismatches:
        # A pinned CSV contains mixed newlines: Git clean conversion would alter its exact blob.
        # Accept the raw pinned blob as well as Git's normal checkout conversion.
        raw = run(["git", "hash-object", "--no-filters", "--stdin-paths"], cwd=source,
                  input_text="\n".join(p for p, _ in mismatches) + "\n").splitlines()
        require(raw == [wanted for _, wanted in mismatches], "Upstream working files differ from the pinned commit")
    chart = (source / "helm/michelangelo/Chart.yaml").read_text(encoding="utf-8")
    require(re.search(r"^version:\s*0\.11\.0\s*$", chart, re.M), "Pinned chart version mismatch")
    return {"revision": REVISION, "chart_version": CHART_VERSION, "tracked_files": len(paths)}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generated(root=ROOT):
    return root / ".generated"


def values(config):
    p = config["platform"]
    result = {key: copy.deepcopy(p[key]) for key in ("metadataStorage", "objectStorage", "ui")}
    result["objectStorage"].pop("auth")
    result["workflow"] = {key: p["workflow"][key] for key in ("engine", "endpoint", "domain")}
    result["images"] = {}
    for service, image in p["images"].items():
        repository, tag = image.split(":", 1)
        result["images"][service] = {"repository": repository, "tag": tag}
    result.update({"fullnameOverride": config["deployment"]["release"], "cadence": {"enabled": False}, "temporal": {"enabled": False},
                   "apiserver": {"service": {"type": "ClusterIP"}, "ingress": {"enabled": False}, "metadataStorage": {"enable": True}, "crdSync": {"enableUpdate": False}},
                   "envoy": {"service": {"type": "ClusterIP"}, "ingress": {"enabled": False}, "corsOrigins": "^http://(localhost|127\\.0\\.0\\.1):8080$"},
                   "controllermgr": {"watchNamespace": [], "metadataStorage": {"enable": True}},
                   "monitoring": {"enabled": False}})
    if p["workflow"]["mode"] == "bundled":
        router = p["mysqlRouter"]
        stores = {}
        for store, database in (("default", "temporal"), ("visibility", "temporal_visibility")):
            stores[store] = {"driver": "sql", "sql": {"driver": "mysql8", "host": router["host"], "port": router["port"],
                "database": database, "user": p["metadataStorage"]["user"], "password": "", "existingSecret": p["metadataStorage"]["existingSecret"],
                "maxConns": 5, "tls": {"enabled": True, "caFile": "/mysql-ca/" + router["caKey"], "serverName": router["host"], "enableHostVerification": True}}}
        result["temporal"] = {"enabled": True, "fullnameOverride": config["deployment"]["release"] + "-temporal",
            "server": {"replicaCount": 1, "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": "512Mi"}},
                       "config": {"numHistoryShards": 16 if config["terraform"]["environment"] == "dev" else 512, "persistence": stores},
                       "additionalVolumes": [{"name": "mysql-ca", "secret": {"secretName": router["caSecret"]}}],
                       "additionalVolumeMounts": [{"name": "mysql-ca", "mountPath": "/mysql-ca", "readOnly": True}]},
            "schema": {"createDatabase": {"enabled": True}, "setup": {"enabled": True}, "update": {"enabled": True}},
            "admintools": {"enabled": False, "image": {"repository": "temporalio/admin-tools", "tag": "1.24.2-tctl-1.18.1-cli-0.13.0", "pullPolicy": "IfNotPresent"}},
            "web": {"enabled": False}, "cassandra": {"enabled": False}, "mysql": {"enabled": False}, "postgresql": {"enabled": False},
            "elasticsearch": {"enabled": False}, "prometheus": {"enabled": False}, "grafana": {"enabled": False}}
    return result


def router_env(config):
    router = config["platform"]["mysqlRouter"]
    env = os.environ.copy()
    env.update({"MYSQL_ROUTER_IMAGE": router["image"], "MYSQL_ROUTER_HOST": router["host"], "MYSQL_ROUTER_PORT": str(router["port"]),
                "MYSQL_ROUTER_CA_SECRET": router["caSecret"], "MYSQL_ROUTER_CA_KEY": router["caKey"], "MYSQL_ROUTER_AUTH_SECRET": router["authSecret"], "MYSQL_ROUTER_REQUIRE_TARGET": "1"})
    return env


def helm_renderer_args(config, root=ROOT):
    script = root / "deploy/postrenderer.py"
    require(script.is_file(), "MySQL TLS post-renderer missing")
    python = config["platform"]["mysqlRouter"]["python"]
    run([python, "-c", "import yaml; assert yaml.__version__ == '6.0.3', 'Install pinned renderer requirements'"])
    return ["--post-renderer", python, "--post-renderer-args", str(script)]


def renderer_digest(root):
    return hashlib.sha256((tree_digest(root / "security/mysql-router") + tree_digest(root / "deploy")).encode()).hexdigest()


def prepare_chart(config, root=ROOT):
    source = source_path(config, root)
    verify_source(source)
    destination = generated(root) / "chart"
    # Archive commit objects instead of copying untracked or changed files.
    archive = run(["git", "-C", source, "archive", "--format=tar", REVISION, "helm/michelangelo"], input_bytes=b"")
    with tempfile.TemporaryDirectory(dir=root / "deploy") as work:
        tar_path = Path(work) / "source.tar"
        tar_path.write_bytes(archive)
        with tarfile.open(tar_path) as tar:
            tar.extractall(work, filter="data")
        staged = Path(work) / "helm/michelangelo"
        run(["helm", "repo", "add", "cadence", "https://cadence-workflow.github.io/cadence-charts"])
        run(["helm", "repo", "add", "temporal", "https://go.temporal.io/helm-charts"])
        run(["helm", "dependency", "build", staged])
        for filename, digest in DEPENDENCY_SHA256.items():
            require(file_digest(staged / "charts" / filename) == digest, "Downloaded dependency archive SHA256 differs from the reviewed lock")
        require(not destination.exists(), "Generated chart already exists; use a fresh .generated/chart directory after reviewing/removing it locally")
        shutil.copytree(staged, destination)
    return destination


def fetch(config, root=ROOT):
    destination = source_path(config, root)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", REPOSITORY, destination])
        run(["git", "-C", destination, "checkout", "--detach", REVISION])
    verify_source(destination)
    chart = generated(root) / "chart"
    if not chart.exists():
        prepare_chart(config, root)
    else:
        verify_chart(chart, destination)
    print("Pinned source verified; chart dependencies prepared locally.")


def verify_chart(chart, source):
    require(chart.is_dir(), "Generated chart missing; run fetch with Helm installed")
    files = run(["git", "-C", source, "ls-tree", "-r", "--name-only", REVISION, "helm/michelangelo"]).splitlines()
    for name in files:
        relative = Path(name).relative_to("helm/michelangelo")
        original, copied = source / name, chart / relative
        require(copied.is_file() and not copied.is_symlink(), "Generated chart tracked file missing")
        require(original.read_bytes().replace(b"\r\n", b"\n") == copied.read_bytes().replace(b"\r\n", b"\n"), "Generated chart differs from pinned upstream")
    allowed = {Path(n).relative_to("helm/michelangelo").as_posix() for n in files}
    for filename, digest in DEPENDENCY_SHA256.items():
        require(file_digest(chart / "charts" / filename) == digest, "Chart dependency archive changed from the reviewed lock")
    for item in chart.rglob("*"):
        if item.is_file():
            name = item.relative_to(chart).as_posix()
            require(name in allowed or name in ("charts/cadence-1.1.0.tgz", "charts/temporal-0.44.0.tgz"), "Unexpected generated chart file")


def render(config, manifests=False, root=ROOT):
    validate(config)
    verify_source(source_path(config, root))
    output = generated(root)
    write_json(output / "values.json", values(config))
    write_json(output / "terraform.tfvars.json", config["terraform"])
    receipt = {"config_sha256": fingerprint(config), "source_revision": REVISION,
               "values_sha256": file_digest(output / "values.json"), "tfvars_sha256": file_digest(output / "terraform.tfvars.json")}
    if manifests:
        chart = output / "chart"
        verify_chart(chart, source_path(config, root))
        text = run(["helm", "template", config["deployment"]["release"], chart, "--namespace", config["deployment"]["namespace"],
                    "--kube-version", config["terraform"]["kubernetes_version"].lstrip("v"), "--include-crds", "-f", output / "values.json",
                    *helm_renderer_args(config, root)], env=router_env(config))
        require(re.search(r"^kind:\s*Secret\s*$", text, re.M) is None, "Rendered credentials Secret rejected; only existingSecret references are permitted")
        (output / "manifest.yaml").write_text(text, encoding="utf-8")
        receipt.update({"manifest_sha256": file_digest(output / "manifest.yaml"), "chart_sha256": tree_digest(chart),
                        "renderer_sha256": renderer_digest(root)})
    write_json(output / "render-receipt.json", receipt)
    print("Secret-free values and Terraform inputs rendered." + (" Post-rendered manifest ready for review." if manifests else " Use --manifests after fetch to review Kubernetes objects."))
    if manifests:
        print("Manifest SHA256: " + receipt["manifest_sha256"])


def ready(config, *, platform=False):
    require(config["deployment"]["mysql_mvp_approved"] is True, "MySQL MVP decision must be explicitly approved")
    text = json.dumps(config["platform"] if platform else config["terraform"])
    require("REPLACE" not in text.upper() and "EXAMPLE" not in text.upper(), "Replace example infrastructure and platform inputs before deployment")
    require(bool(config["terraform"]["availability_domains"]), "availability_domains cannot be empty for deployment")
    if platform:
        require(config["deployment"]["static_object_storage_credentials_approved"] is True, "Static S3 credential MVP exception must be explicitly approved")
        if config["platform"]["workflow"]["mode"] == "external":
            require(config["deployment"]["workflow_namespace_registered"] is True, "Register external Temporal namespace and explicitly attest it before install")
        require(config["deployment"]["router_tls_verified"] is True, "MySQL Router TLS runtime verification must pass before install")
        require("REPLACE" not in config["deployment"]["kube_context"].upper() and "REPLACE" not in config["deployment"]["kube_api_server"].upper(), "Set the exact Kubernetes context and API server")


def mutation_gate(config, allowed, confirmation, expected):
    require(config["deployment"]["enable_mutations"] is True, "deployment.enable_mutations is false; deployment blocked")
    require(allowed is True, "Explicit CLI mutation authorization is required")
    require(confirmation == expected, "Confirmation does not match the configured deployment target")


def terraform_env(config):
    env = os.environ.copy()
    require(not any(k.upper().startswith("TF_VAR_") and k.upper() != "TF_VAR_MYSQL_ADMIN_PASSWORD" for k in env), "Unexpected TF_VAR override rejected; central config owns Terraform inputs")
    require(not any(k.upper().startswith("TF_CLI_ARGS") for k in env), "Terraform CLI environment overrides rejected")
    # os.environ uppercases names on Windows; Terraform variable names remain case-sensitive.
    secret = next((env.pop(k) for k in list(env) if k.upper() == "TF_VAR_MYSQL_ADMIN_PASSWORD"), None)
    if secret is not None:
        env["TF_VAR_mysql_admin_password"] = secret
    if config["terraform"]["database_mode"] == "mysql":
        require(bool(env.get("TF_VAR_mysql_admin_password")), "TF_VAR_mysql_admin_password must be set; never put it in JSON or command arguments")
    env["TF_IN_AUTOMATION"] = "1"
    return env


def validate_shutdown_plan(reviewed):
    changes = reviewed.get("resource_changes", [])
    require(not any(change["type"] == "oci_objectstorage_bucket" and "delete" in change["change"]["actions"] for change in changes),
            "Shutdown plan would delete an artifact bucket; refusing")
    require(all(set(change["change"]["actions"]) <= {"delete", "no-op", "read"} for change in changes), "Unexpected non-shutdown resource mutation")


def terraform_plan(config, args, root=ROOT):
    ready(config)
    require(args.allow_cloud_read, "terraform plan contacts OCI; explicitly pass --allow-cloud-read")
    require(args.ack_sensitive_local_artifacts, "Terraform state and plans can contain secrets; pass --ack-sensitive-local-artifacts after protecting local storage")
    env = terraform_env(config)
    output = generated(root)
    write_json(output / "terraform.tfvars.json", config["terraform"])
    run(["terraform", "init", "-input=false"], cwd=root / "terraform", env=env)
    plan = output / "deployment.tfplan"
    require(not plan.exists(), "Saved plan already exists; review/remove it locally before replacing")
    command = ["terraform", "plan", "-input=false", "-no-color", "-var-file=" + str(output / "terraform.tfvars.json"), "-out=" + str(plan)]
    shutdown = getattr(args, "shutdown_transient", False)
    if shutdown:
        require(config["terraform"]["environment"] == "dev", "Transient shutdown is supported only for disposable dev deployments")
        addresses = run(["terraform", "state", "list"], cwd=root / "terraform", env=env).splitlines()
        targets = [address for address in addresses if address.startswith("oci_") and address != "oci_objectstorage_bucket.artifacts"]
        require(bool(targets), "No transient managed resources found")
        command.extend(["-destroy", *["-target=" + address for address in targets]])
    run(command, cwd=root / "terraform", env=env)
    if shutdown:
        reviewed = json.loads(run(["terraform", "show", "-json", plan], cwd=root / "terraform", env=env))
        validate_shutdown_plan(reviewed)
    write_json(output / "plan-receipt.json", {"config_sha256": fingerprint(config), "plan_sha256": file_digest(plan),
               "terraform_sha256": tree_digest(root / "terraform"), "target_compartment": config["terraform"]["compartment_ocid"],
               "operation": "shutdown" if shutdown else "deploy"})
    print("Plan saved locally; may contain secrets. Review with terraform show privately, then supply its SHA256 to apply.")
    print("Plan SHA256: " + file_digest(plan))


def terraform_apply(config, args, root=ROOT):
    ready(config)
    mutation_gate(config, args.allow_paid_resources or getattr(args, "allow_shutdown", False), args.confirm_target, config["terraform"]["compartment_ocid"])
    require(args.ack_sensitive_local_artifacts, "Explicit acknowledgement of sensitive Terraform artifacts required")
    output = generated(root)
    receipt = read_json(output / "plan-receipt.json")
    shutdown = receipt.get("operation", "deploy") == "shutdown"
    require(shutdown or args.allow_paid_resources, "Deployment apply requires --allow-paid-resources")
    require(not shutdown or (getattr(args, "allow_shutdown", False) and config["terraform"]["environment"] == "dev"), "Shutdown apply requires --allow-shutdown for the dev profile")
    plan = output / "deployment.tfplan"
    require(receipt["config_sha256"] == fingerprint(config), "Configuration changed since plan; generate and review a new plan")
    require(receipt["terraform_sha256"] == tree_digest(root / "terraform"), "Terraform source changed since plan")
    require(receipt["plan_sha256"] == file_digest(plan) == args.reviewed_plan_sha256, "Saved plan SHA256 does not match explicit review")
    run(["terraform", "apply", "-input=false", "-no-color", plan], cwd=root / "terraform", env=terraform_env(config))
    print("Reviewed transient shutdown applied; artifact bucket remains managed and retained." if shutdown else "Saved reviewed plan applied; inspect outputs locally and update endpoint references before install.")


def kube(config, *args):
    return ["kubectl", "--context", config["deployment"]["kube_context"], "--namespace", config["deployment"]["namespace"], *args]


def cluster_identity_checks(config):
    context = json.loads(run(kube(config, "config", "view", "--minify", "-o", "json")))
    require(context["clusters"][0]["cluster"]["server"].rstrip("/") == config["deployment"]["kube_api_server"].rstrip("/"), "Kubernetes context points to an unexpected API server")
    require(not context["clusters"][0]["cluster"].get("insecure-skip-tls-verify", False), "Kubernetes TLS verification cannot be disabled")
    version = json.loads(run(kube(config, "version", "-o", "json")))["serverVersion"]
    major, minor = int(version["major"]), int(re.match(r"\d+", version["minor"])[0])
    require((major, minor) >= (1, 29), "Restartable MySQL Router init sidecar requires Kubernetes >=1.29")
    run(kube(config, "get", "namespace", config["deployment"]["namespace"], "-o", "name"))


def cluster_checks(config):
    cluster_identity_checks(config)
    p = config["platform"]
    for name, required_keys in ((p["metadataStorage"]["existingSecret"], ["password"]),
                                (p["objectStorage"]["existingSecret"], ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]),
                                (p["mysqlRouter"]["caSecret"], [p["mysqlRouter"]["caKey"]]),
                                (p["mysqlRouter"]["authSecret"], ["tls.crt", "tls.key"])):
        secret = json.loads(run(kube(config, "get", "secret", name, "-o", "json")))
        require(all(secret.get("data", {}).get(key) for key in required_keys), "Existing Secret missing required nonempty keys")


def sync_secrets(config):
    """Explicit optional installation step: stdin only, no literals in argv or files."""
    import base64
    p = config["platform"]
    required_env = ("MYSQL_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "MYSQL_CA_FILE", "MYSQL_ROUTER_AUTH_CERT_FILE", "MYSQL_ROUTER_AUTH_KEY_FILE")
    require(all(os.environ.get(k) for k in required_env), "Secret sync requires MYSQL_PASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, MYSQL_CA_FILE, MYSQL_ROUTER_AUTH_CERT_FILE, MYSQL_ROUTER_AUTH_KEY_FILE")
    ca = Path(os.environ["MYSQL_CA_FILE"]).read_bytes()
    cert = Path(os.environ["MYSQL_ROUTER_AUTH_CERT_FILE"]).read_bytes()
    key = Path(os.environ["MYSQL_ROUTER_AUTH_KEY_FILE"]).read_bytes()
    require(b"BEGIN CERTIFICATE" in ca and b"PRIVATE KEY" not in ca, "MySQL CA file must contain public certificates only")
    require(b"BEGIN CERTIFICATE" in cert and b"PRIVATE KEY" in key, "Router authentication certificate/key files invalid")
    entries = [(p["metadataStorage"]["existingSecret"], {"password": os.environ["MYSQL_PASSWORD"].encode()}, "Opaque"),
               (p["objectStorage"]["existingSecret"], {"AWS_ACCESS_KEY_ID": os.environ["AWS_ACCESS_KEY_ID"].encode(), "AWS_SECRET_ACCESS_KEY": os.environ["AWS_SECRET_ACCESS_KEY"].encode()}, "Opaque"),
               (p["mysqlRouter"]["caSecret"], {p["mysqlRouter"]["caKey"]: ca}, "Opaque"),
               (p["mysqlRouter"]["authSecret"], {"tls.crt": cert, "tls.key": key}, "kubernetes.io/tls")]
    docs = [{"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": config["deployment"]["namespace"]}, "type": typ,
             "data": {field: base64.b64encode(value).decode() for field, value in data.items()}} for name, data, typ in entries]
    run(kube(config, "apply", "--server-side", "--field-manager=michelangelo-installer", "-f", "-"),
        input_text=json.dumps({"apiVersion": "v1", "kind": "List", "items": docs}))


def remove_completed_setup_jobs(config):
    if config["platform"]["workflow"]["mode"] != "bundled":
        return
    jobs = json.loads(run(kube(config, "get", "jobs", "-o", "json")))
    names = {config["deployment"]["release"] + "-temporal-schema",
             config["deployment"]["release"] + "-temporal-namespace-setup"}
    selected = [job for job in jobs["items"] if job["metadata"]["name"] in names]
    require(all(any(condition.get("type") == "Complete" and condition.get("status") == "True"
                    for condition in job.get("status", {}).get("conditions", [])) for job in selected),
            "Existing Temporal setup Job is not complete; inspect it before upgrading")
    for job in selected:
        run(kube(config, "delete", "job", job["metadata"]["name"], "--wait=true"))


def install(config, args, root=ROOT):
    ready(config, platform=True)
    mutation_gate(config, args.allow_cluster_mutation, args.confirm_target, config["deployment"]["kube_context"])
    output = generated(root)
    receipt = read_json(output / "render-receipt.json")
    require(receipt.get("manifest_sha256") == args.reviewed_manifest_sha256 == file_digest(output / "manifest.yaml"), "Explicitly review the rendered manifest SHA256 before install")
    require(receipt["config_sha256"] == fingerprint(config), "Configuration changed since render; review a new manifest")
    verify_source(source_path(config, root))
    verify_chart(output / "chart", source_path(config, root))
    require(receipt["chart_sha256"] == tree_digest(output / "chart"), "Chart/dependencies changed since render")
    require(receipt["renderer_sha256"] == renderer_digest(root), "TLS post-renderer changed since render")
    require(receipt["values_sha256"] == file_digest(output / "values.json"), "Rendered values changed since review")
    cluster_identity_checks(config)
    if args.sync_secrets:
        sync_secrets(config)
    cluster_checks(config)
    remove_completed_setup_jobs(config)
    run(["helm", "upgrade", "--install", config["deployment"]["release"], output / "chart", "--kube-context", config["deployment"]["kube_context"],
         "--namespace", config["deployment"]["namespace"], "-f", output / "values.json", "--atomic", "--wait", "--timeout", str(config["deployment"]["timeout_seconds"]) + "s",
         *helm_renderer_args(config, root)], env=router_env(config))
    smoke(config)
    print("Helm release installed and Deployment rollout checks passed. Functional workflow/object-storage checks remain operator prerequisites.")


def smoke(config):
    ready(config, platform=True)
    cluster_checks(config)
    for component in SERVICE_DIGESTS:
        run(kube(config, "rollout", "status", "deployment/" + config["deployment"]["release"] + "-" + component,
                 "--timeout=" + str(config["deployment"]["timeout_seconds"]) + "s"))
    if config["platform"]["workflow"]["mode"] == "bundled":
        for component in ("frontend", "history", "matching", "worker"):
            run(kube(config, "rollout", "status", "deployment/" + config["deployment"]["release"] + "-temporal-" + component,
                     "--timeout=" + str(config["deployment"]["timeout_seconds"]) + "s"))
        run(kube(config, "wait", "--for=condition=complete", "job/" + config["deployment"]["release"] + "-temporal-namespace-setup",
                 "--timeout=" + str(config["deployment"]["timeout_seconds"]) + "s"))
    print("Four core Deployment rollouts passed; this does not prove end-to-end workflow, artifact access, TLS identity, or production readiness.")


def doctor(config):
    validate(config)
    for name in ("git", "helm", "terraform", "kubectl", "oci"):
        available = shutil.which(name) or (name == "helm" and (ROOT / ".local/bin/helm/windows-amd64/helm.exe").is_file())
        print(f"{name}: {'available' if available else 'missing'}")
    print("source: " + json.dumps(verify_source(source_path(config))))
    print("mutations: " + ("enabled in config; CLI confirmation still required" if config["deployment"]["enable_mutations"] else "blocked"))
    print("Prerequisites: private OKE API route, existing Secrets, verified Router TLS; external Temporal requires a registered namespace.")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=ROOT / "config/config.json")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    config = sub.add_parser("config")
    config.add_argument("action", choices=["validate"])
    sub.add_parser("fetch")
    render_parser = sub.add_parser("render")
    render_parser.add_argument("--manifests", action="store_true")
    tf = sub.add_parser("terraform")
    tfsub = tf.add_subparsers(dest="action", required=True)
    plan = tfsub.add_parser("plan")
    plan.add_argument("--allow-cloud-read", action="store_true")
    plan.add_argument("--ack-sensitive-local-artifacts", action="store_true")
    plan.add_argument("--shutdown-transient", action="store_true", help="Plan dev resource deletion while retaining the artifact bucket")
    apply = tfsub.add_parser("apply")
    apply.add_argument("--allow-paid-resources", action="store_true")
    apply.add_argument("--allow-shutdown", action="store_true", help="Authorize applying an explicitly reviewed dev shutdown plan")
    apply.add_argument("--ack-sensitive-local-artifacts", action="store_true")
    apply.add_argument("--confirm-target")
    apply.add_argument("--reviewed-plan-sha256")
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--allow-cluster-mutation", action="store_true")
    install_parser.add_argument("--confirm-target")
    install_parser.add_argument("--reviewed-manifest-sha256")
    install_parser.add_argument("--sync-secrets", action="store_true", help="Explicitly create/update configured Secrets from environment and certificate files using stdin")
    sub.add_parser("smoke")
    return p


def main():
    args = parser().parse_args()
    try:
        config = validate(read_json(args.config))
        lock = read_json(ROOT / "upstream.lock.json")
        require(lock.get("revision") == REVISION and lock.get("repository") == REPOSITORY and lock.get("chart_version") == CHART_VERSION,
                "upstream.lock.json must match the installer immutable source pin")
        if args.command == "doctor":
            doctor(config)
        elif args.command == "config":
            print("Configuration valid; deployment prerequisites and authorization are checked separately.")
        elif args.command == "fetch":
            fetch(config)
        elif args.command == "render":
            render(config, args.manifests)
        elif args.command == "terraform":
            (terraform_plan if args.action == "plan" else terraform_apply)(config, args)
        elif args.command == "install":
            install(config, args)
        elif args.command == "smoke":
            smoke(config)
        return 0
    except (InstallerError, OSError, KeyError, TypeError, ValueError) as exc:
        # Unexpected JSON/tool shapes do not get echoed: they may contain credentials.
        message = str(exc) if isinstance(exc, InstallerError) else "Local artifact or external tool response invalid; operation stopped"
        print("ERROR: " + message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
