#!/usr/bin/env python3
"""Fail-closed Helm transport transform for the pinned Michelangelo chart.

Only chart-labelled apiserver/controllermgr Deployments with a mounted matching
base.yaml are eligible. No chart source or application credentials are changed.
"""
import hashlib
import os
import re
import sys

import yaml

COMPONENTS = {"apiserver": "apiserver", "controllermgr": "app"}
ROUTER = "metadata-mysql-router"
ANNOTATION = "security.michelangelo.ai/mysql-router-sha256"
IMAGE_RE = re.compile(r"^container-registry\.oracle\.com/mysql/community-router(?::[\w.-]+)?@sha256:[0-9a-f]{64}$")
DNS_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


class TransportError(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    """Reject ambiguous YAML instead of silently accepting the last key."""


def strict_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, (str, int, float, bool)) or key in result:
            raise TransportError("duplicate or invalid YAML mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, strict_mapping)


def load(text):
    try:
        return list(yaml.load_all(text, Loader=StrictLoader))
    except yaml.YAMLError as exc:
        raise TransportError("malformed YAML") from exc


def require(condition, message):
    if not condition:
        raise TransportError(message)


def settings(env):
    image = env.get("MYSQL_ROUTER_IMAGE", "")
    host = env.get("MYSQL_ROUTER_HOST", "")
    secret = env.get("MYSQL_ROUTER_CA_SECRET", "")
    auth_secret = env.get("MYSQL_ROUTER_AUTH_SECRET", "")
    key = env.get("MYSQL_ROUTER_CA_KEY", "ca.pem")
    require(bool(IMAGE_RE.fullmatch(image)), "MYSQL_ROUTER_IMAGE must be stock community-router pinned by sha256 digest")
    require(bool(DNS_RE.fullmatch(host)) and host != "localhost.localdomain" and not re.fullmatch(r"[0-9.]+", host), "MYSQL_ROUTER_HOST must be a remote DNS hostname, not an IP or config text")
    require(len(secret) <= 253 and bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", secret)), "MYSQL_ROUTER_CA_SECRET must name an existing Kubernetes Secret")
    require(len(auth_secret) <= 253 and bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", auth_secret)), "MYSQL_ROUTER_AUTH_SECRET must name an existing Router RSA certificate/key Secret")
    require(auth_secret != secret, "Router RSA auth Secret must be distinct from database CA Secret")
    require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", key)) and len(key) <= 253, "MYSQL_ROUTER_CA_KEY is invalid")
    port_text = env.get("MYSQL_ROUTER_PORT", "3306")
    require(bool(re.fullmatch(r"[0-9]{1,5}", port_text)), "MYSQL_ROUTER_PORT must be numeric")
    port = int(port_text)
    require(1 <= port <= 65535, "MYSQL_ROUTER_PORT out of range")
    require(env.get("MYSQL_ROUTER_REQUIRE_TARGET", "1") in {"0", "1"}, "MYSQL_ROUTER_REQUIRE_TARGET must be 0 or 1")
    return image, host, port, secret, key, auth_secret


def router_config(host, port):
    return f"""[DEFAULT]
logging_folder =
runtime_folder = /router/state
data_folder = /router/state
unknown_config_option = error

[logger]
level = INFO

[routing:metadata]
bind_address = 127.0.0.1
bind_port = 6446
destinations = {host}:{port}
routing_strategy = first-available
protocol = classic
client_ssl_mode = PREFERRED
client_ssl_cert = /router/auth/tls.crt
client_ssl_key = /router/auth/tls.key
server_ssl_mode = REQUIRED
server_ssl_verify = VERIFY_IDENTITY
server_ssl_ca = /router/ca/ca.pem
"""


def named(items, name):
    require(isinstance(items, list) and all(isinstance(item, dict) for item in items), "expected list of objects")
    matches = [item for item in items if item.get("name") == name]
    require(len(matches) <= 1, f"duplicate {name}")
    return matches[0] if matches else None


def env_value(container, name):
    entry = named(container.get("env", []), name)
    return entry.get("value") if entry else None


def resource_key(doc):
    meta = doc.get("metadata", {})
    require(isinstance(meta, dict) and isinstance(meta.get("name"), str), "resource missing metadata.name")
    return doc["kind"], meta.get("namespace", "default"), meta["name"]


def transform(text, env):
    image, host, port, secret, ca_key, auth_secret = settings(env)
    docs = [doc for doc in load(text) if doc is not None]
    require(bool(docs), "empty manifest stream")
    index = {}
    for doc in docs:
        require(isinstance(doc, dict) and isinstance(doc.get("kind"), str) and isinstance(doc.get("apiVersion"), str), "manifest must be a Kubernetes resource mapping")
        key = resource_key(doc)
        require(key not in index, "duplicate Kubernetes resource")
        index[key] = doc
    targets = 0
    for doc in list(docs):
        meta = doc["metadata"]
        labels = meta.get("labels", {})
        component = labels.get("app.kubernetes.io/component")
        if doc["kind"] != "Deployment" or labels.get("app.kubernetes.io/part-of") != "michelangelo" or component not in COMPONENTS:
            continue
        require(doc["apiVersion"] == "apps/v1", "unsupported Deployment apiVersion")
        pod = doc["spec"]["template"]["spec"]
        require(not pod.get("hostNetwork", False), "Router requires an isolated Pod network, hostNetwork is forbidden")
        main = named(pod.get("containers", []), COMPONENTS[component])
        require(main is not None, f"missing upstream {component} container")
        require(named(pod["containers"], ROUTER) is None, "Router must be a native init sidecar, not an app container")
        mount = named(main.get("volumeMounts", []), "config")
        require(mount is not None and mount.get("mountPath") == "/config", "missing upstream /config mount")
        volumes = pod.get("volumes", [])
        volume = named(volumes, "config")
        require(volume is not None and isinstance(volume.get("configMap"), dict), "missing upstream config volume")
        configmap = index.get(("ConfigMap", meta.get("namespace", "default"), volume["configMap"].get("name")))
        require(configmap is not None, "missing upstream ConfigMap in manifest stream")
        base = configmap.get("data", {}).get("base.yaml")
        require(isinstance(base, str), "missing upstream base.yaml")
        parsed = load(base)
        require(len(parsed) == 1 and isinstance(parsed[0], dict), "invalid upstream base.yaml")
        config = parsed[0]
        require(component in config and isinstance(config.get("mysql"), dict), "wrong component configuration")
        require(config["mysql"].get("host") == "127.0.0.1" and str(config["mysql"].get("port")) == "6446", "metadataStorage must use 127.0.0.1:6446; remote plaintext route forbidden")
        inits = pod.setdefault("initContainers", [])
        if component == "apiserver":
            for name in ("wait-for-metadata-storage", "schema-init"):
                init = named(inits, name)
                require(init is not None, f"missing upstream {name}")
                require(env_value(init, "METADATA_HOST") == "127.0.0.1" and str(env_value(init, "METADATA_PORT")) == "6446", f"{name} must connect only to loopback Router")
                require("mysql" in " ".join(init.get("command", [])) and "psql" not in " ".join(init.get("command", [])), "only upstream MySQL driver is supported")
        group = pod.setdefault("securityContext", {}).setdefault("fsGroup", 65534)
        require(isinstance(group, int) and not isinstance(group, bool) and group > 0, "non-root fsGroup required for writable Router state")
        conf = router_config(host, port)
        digest = hashlib.sha256(conf.encode()).hexdigest()
        cm_name = meta["name"][:40].rstrip("-") + "-router-" + hashlib.sha256(meta["name"].encode()).hexdigest()[:12]
        router = {
            "name": ROUTER, "image": image, "imagePullPolicy": "IfNotPresent",
            "restartPolicy": "Always", "command": ["mysqlrouter"],
            "args": ["--config", "/router/config/mysqlrouter.conf"],
            "securityContext": {"runAsNonRoot": True, "runAsUser": 65534, "runAsGroup": group,
                                "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"}},
            "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "250m", "memory": "128Mi"}},
            "volumeMounts": [{"name": "mysql-router-config", "mountPath": "/router/config", "readOnly": True},
                             {"name": "mysql-router-ca", "mountPath": "/router/ca", "readOnly": True},
                             {"name": "mysql-router-auth", "mountPath": "/router/auth", "readOnly": True},
                             {"name": "mysql-router-state", "mountPath": "/router/state"},
                             {"name": "mysql-router-tmp", "mountPath": "/tmp"}],
            # A kubelet TCP probe originates outside the Pod network; use exec
            # to probe the loopback-only listener in the Router image instead.
            "startupProbe": {"exec": {"command": ["/bin/bash", "-ec", "exec 3<>/dev/tcp/127.0.0.1/6446; exec 3>&-"]},
                             "periodSeconds": 2, "failureThreshold": 60, "timeoutSeconds": 1},
        }
        managed_volumes = [
            {"name": "mysql-router-config", "configMap": {"name": cm_name, "defaultMode": 292}},
            {"name": "mysql-router-ca", "secret": {"secretName": secret, "defaultMode": 292,
                                                   "items": [{"key": ca_key, "path": "ca.pem"}]}},
            {"name": "mysql-router-auth", "secret": {"secretName": auth_secret, "defaultMode": 288,
                                                     "items": [{"key": "tls.crt", "path": "tls.crt"}, {"key": "tls.key", "path": "tls.key"}]}},
            {"name": "mysql-router-state", "emptyDir": {"sizeLimit": "16Mi"}},
            {"name": "mysql-router-tmp", "emptyDir": {"sizeLimit": "16Mi"}},
        ]
        cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": cm_name, "namespace": meta.get("namespace", "default"),
              "labels": {"app.kubernetes.io/part-of": "michelangelo", "app.kubernetes.io/component": "mysql-router"}},
              "data": {"mysqlrouter.conf": conf}}
        old = named(inits, ROUTER)
        if old is not None:
            require(old == router and inits[0] is old, "conflicting or misplaced existing Router sidecar")
        else:
            inits.insert(0, router)
        for wanted in managed_volumes:
            existing = named(volumes, wanted["name"])
            require(existing is None or existing == wanted, f"conflicting {wanted['name']} volume")
            if existing is None:
                volumes.append(wanted)
        pod["volumes"] = volumes
        cm_key = resource_key(cm)
        require(cm_key not in index or index[cm_key] == cm, "conflicting existing Router ConfigMap")
        if cm_key not in index:
            docs.append(cm)
            index[cm_key] = cm
        doc["spec"]["template"].setdefault("metadata", {}).setdefault("annotations", {})[ANNOTATION] = digest
        targets += 1
    require(targets > 0 or env.get("MYSQL_ROUTER_REQUIRE_TARGET", "1") == "0", "no eligible controlplane Deployment found")
    return yaml.safe_dump_all(docs, sort_keys=False, explicit_start=True)


def main():
    try:
        # Helm emits UTF-8 regardless of the Windows terminal code page.
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        text = sys.stdin.read(16 * 1024 * 1024 + 1)
        require(len(text) <= 16 * 1024 * 1024, "manifest stream exceeds 16 MiB")
        output = transform(text, os.environ)
    except (TransportError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        # Do not log input: Helm streams can contain credential Secrets.
        print(f"mysql-router post-renderer: {exc.__class__.__name__}: transport validation failed" if not isinstance(exc, TransportError)
              else f"mysql-router post-renderer: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
