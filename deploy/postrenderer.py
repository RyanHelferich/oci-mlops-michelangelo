#!/usr/bin/env python3
"""Normalize one pinned upstream label defect, then run the TLS transform."""
import importlib.util
import hashlib
import json
from pathlib import Path
import os
import re
import sys

SOURCE = "# Source: michelangelo/templates/core/temporal-namespace-setup-job.yaml"
CRD_BUNDLE_SHA256 = "1e7920b28a706850297def718450633f3e7a6681d70510aa957a7dda01f65e9a"
SUPPORT_IMAGES = {
    "docker.io/library/mysql:8.0": "sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b",
    "docker.io/library/busybox:1.36": "sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662",
    "docker.io/temporalio/server:1.24.2": "sha256:16de74d893e813053b82551eb4463c795b368d2029520b4c8fe2e47e1c336bb9",
    "docker.io/temporalio/admin-tools:1.24.2-tctl-1.18.1-cli-0.13.0": "sha256:a64874edd4fedecb9b6ca74b10cf9512f09522d676ce2af5bdaf06ab7adadd12",
    "docker.io/envoyproxy/envoy:v1.29-latest": "sha256:5a292b91adc87aa56146fb9ee52fc85c30570d0f175d95b48cde8035a2e641dd",
}


def qualify_images(documents):
    """CRI-O cannot resolve upstream short image names in enforcing mode."""
    for document in documents:
        spec = document.get("spec", {})
        pod = spec if document.get("kind") == "Pod" else spec.get("template", {}).get("spec", {})
        for container in pod.get("containers", []) + pod.get("initContainers", []):
            image = container["image"]
            first = image.split("/", 1)[0]
            if "/" not in image:
                container["image"] = "docker.io/library/" + image
            elif "." not in first and ":" not in first and first != "localhost":
                container["image"] = "docker.io/" + image
            if container["image"] in SUPPORT_IMAGES:
                container["image"] += "@" + SUPPORT_IMAGES[container["image"]]
    return documents


def secure_ui(documents):
    """Run the pinned Nginx UI without root, privileged ports or writable image layers."""
    for document in list(documents):
        labels = document.get("metadata", {}).get("labels", {})
        if document.get("kind") != "Deployment" or labels.get("app.kubernetes.io/component") != "ui":
            continue
        pod = document["spec"]["template"]["spec"]
        container = next(item for item in pod["containers"] if item["name"] == "ui")
        name = document["metadata"]["name"] + "-nginx"
        container["command"] = ["nginx", "-c", "/oci-nginx/nginx.conf", "-g", "daemon off;"]
        next(port for port in container["ports"] if port["name"] == "http")["containerPort"] = 8080
        container["securityContext"]["readOnlyRootFilesystem"] = True
        container.setdefault("volumeMounts", []).extend([
            {"name": "oci-nginx", "mountPath": "/oci-nginx", "readOnly": True},
            {"name": "oci-nginx-tmp", "mountPath": "/tmp"}])
        pod.setdefault("volumes", []).extend([
            {"name": "oci-nginx", "configMap": {"name": name}},
            {"name": "oci-nginx-tmp", "emptyDir": {"sizeLimit": "64Mi"}}])
        config = """worker_processes auto;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 1024; }
http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  access_log /dev/stdout;
  client_body_temp_path /tmp/client_temp;
  proxy_temp_path /tmp/proxy_temp;
  fastcgi_temp_path /tmp/fastcgi_temp;
  uwsgi_temp_path /tmp/uwsgi_temp;
  scgi_temp_path /tmp/scgi_temp;
  server {
    listen 8080;
    location / {
      root /usr/share/nginx/html;
      index index.html index.htm;
      try_files $uri $uri/ /index.html;
    }
  }
}
"""
        documents.append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
            "name": name, "namespace": document["metadata"]["namespace"]}, "data": {"nginx.conf": config}})
    return documents


def install_crds(documents):
    directory = Path(__file__).resolve().parent / "crds"
    files = sorted(directory.glob("*.json"))
    if len(files) != 11:
        raise ValueError("Pinned upstream CRD bundle missing")
    digest = hashlib.sha256()
    for filename in files:
        digest.update(filename.name.encode())
        digest.update(hashlib.sha256(filename.read_bytes().replace(b"\r\n", b"\n")).digest())
    if digest.hexdigest() != CRD_BUNDLE_SHA256:
        raise ValueError("Pinned upstream CRD bundle changed")
    for filename in files:
        resource = json.loads(filename.read_text())
        if resource.get("kind") != "CustomResourceDefinition" or resource["spec"]["group"] != "michelangelo.api":
            raise ValueError("Unexpected CRD bundle content")
        resource["metadata"]["annotations"] = {"helm.sh/resource-policy": "keep"}
        documents.append(resource)
    for document in documents:
        if document.get("kind") == "ClusterRole":
            document["rules"] = [rule for rule in document["rules"] if rule.get("apiGroups") != ["apiextensions.k8s.io"]]
            for rule in document["rules"]:
                if "namespaces" in rule.get("resources", []):
                    rule["verbs"] = [verb for verb in rule["verbs"] if verb in ("get", "list", "watch", "create")]
    return documents


def normalize_namespace_labels(text):
    documents = re.split(r"(?m)^---\s*$", text)
    for index, document in enumerate(documents):
        if SOURCE not in document:
            continue
        if not re.search(r"(?m)^kind: Job\s*$", document):
            raise ValueError("Pinned namespace setup source is not a Job")
        match = re.search(r"(?ms)^metadata:\s*\n(?P<metadata>.*?)^spec:\s*\n", document)
        if match is None:
            raise ValueError("Pinned namespace Job metadata shape changed")
        metadata = match["metadata"]
        name = re.search(r"(?m)^  name: ([a-z0-9-]+)-temporal-namespace-setup\s*$", metadata)
        if name is None:
            raise ValueError("Pinned namespace Job name changed")
        expected = {"app.kubernetes.io/name": ["michelangelo", "temporal"],
                    "app.kubernetes.io/component": ["temporal-namespace-setup", "schema"]}
        for key, pair in expected.items():
            pattern = rf"(?m)^    {re.escape(key)}: ([^\r\n]+)\r?$"
            matches = list(re.finditer(pattern, metadata))
            if len(matches) == 1:
                continue
            if [m[1].strip() for m in matches] != pair:
                raise ValueError("Unexpected duplicate namespace Job label")
            start, end = matches[0].span()
            metadata = metadata[:start] + metadata[end:].lstrip("\r\n")
        documents[index] = document[:match.start("metadata")] + metadata + document[match.end("metadata"):]
    return "---".join(documents)


def main():
    try:
        text = sys.stdin.read(16 * 1024 * 1024 + 1)
        if len(text) > 16 * 1024 * 1024:
            raise ValueError("Manifest stream too large")
        script = Path(__file__).resolve().parents[1] / "security/mysql-router/post-renderer.py"
        spec = importlib.util.spec_from_file_location("mysql_router_renderer", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = module.transform(normalize_namespace_labels(text), os.environ)
        output = module.yaml.safe_dump_all(install_crds(secure_ui(qualify_images(module.load(output)))), sort_keys=False, explicit_start=True)
        sys.stdout.write(output)
        return 0
    except Exception:
        # No input or exception details: Helm streams can contain credentials.
        print("Platform post-renderer validation failed; no manifest emitted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
