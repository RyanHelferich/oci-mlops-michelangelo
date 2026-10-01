#!/usr/bin/env python3
"""Generate a short-lived development CA/server certificate and optionally import it.

Production must use the organization's certificate issuance and renewal process.
Private keys and import payloads stay in .local/pki, never in Terraform variables.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def execute(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        # Import payloads contain private keys; do not echo tool diagnostics.
        raise RuntimeError(f"{Path(argv[0]).name} failed (exit {result.returncode}); no sensitive output shown")
    return result.stdout


def secure_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config/config.json"))
    parser.add_argument("--openssl", default=shutil.which("openssl"))
    parser.add_argument("--import-oci", action="store_true")
    args = parser.parse_args()
    if not args.openssl:
        parser.error("OpenSSL is required; provide --openssl with its executable path")
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    terraform = config["terraform"]
    deployment = config["deployment"]
    if terraform.get("environment", "dev") != "dev":
        parser.error("Development certificate generation is allowed only for environment=dev")
    hostname = terraform.get("mysql_hostname_label", "mysql")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,29}", hostname):
        parser.error("Invalid MySQL hostname label")
    # Matches terraform/network.tf: subnet DNS label db, VCN DNS label madev.
    fqdn = hostname + ".db.madev.oraclevcn.com"
    directory = ROOT / ".local/pki" / hostname
    if directory.exists():
        parser.error("Certificate directory already exists; review it before rotating or regenerating")
    directory.mkdir(parents=True, mode=0o700)
    openssl = str(Path(args.openssl).resolve())
    execute([openssl, "req", "-x509", "-newkey", "rsa:4096", "-nodes", "-sha256", "-days", "365",
             "-subj", "/CN=OCI Michelangelo Development CA", "-keyout", str(directory / "ca.key"),
             "-out", str(directory / "ca.pem"), "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign"])
    execute([openssl, "req", "-new", "-newkey", "rsa:4096", "-nodes", "-sha256", "-subj", "/CN=" + fqdn,
             "-keyout", str(directory / "server.key"), "-out", str(directory / "server.csr")])
    secure_write(directory / "extensions.cnf", "basicConstraints=critical,CA:FALSE\n"
                 "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                 "subjectAltName=DNS:" + fqdn + "\n")
    execute([openssl, "x509", "-req", "-in", str(directory / "server.csr"), "-CA", str(directory / "ca.pem"),
             "-CAkey", str(directory / "ca.key"), "-CAcreateserial", "-sha256", "-days", "30",
             "-extfile", str(directory / "extensions.cnf"), "-out", str(directory / "server.pem")])
    execute([openssl, "verify", "-CAfile", str(directory / "ca.pem"), "-verify_hostname", fqdn,
             str(directory / "server.pem")])
    for path in directory.iterdir():
        if path.suffix in {".key", ".csr"}:
            path.chmod(0o600)
    metadata = {"hostname": fqdn, "ca_file": str(directory / "ca.pem"), "validity_days": 30,
                "certificate_ocid": None}
    if args.import_oci:
        if deployment.get("enable_mutations") is not True:
            parser.error("OCI import requires deployment.enable_mutations=true in the local configuration")
        oci = shutil.which("oci")
        if not oci:
            parser.error("OCI CLI is required for certificate import")
        payload = {
            "compartmentId": terraform["compartment_ocid"],
            "name": "michelangelo-dev-mysql-" + __import__("uuid").uuid4().hex[:8],
            "description": "30-day development-only database TLS certificate",
            "certificatePem": (directory / "server.pem").read_text(encoding="utf-8"),
            "certChainPem": (directory / "ca.pem").read_text(encoding="utf-8"),
            "privateKeyPem": (directory / "server.key").read_text(encoding="utf-8"),
        }
        payload_path = directory / "import.json"
        secure_write(payload_path, json.dumps(payload))
        try:
            imported = json.loads(execute([
                oci, "certs-mgmt", "certificate", "create-by-importing-config",
                "--from-json", "file://" + str(payload_path), "--profile", terraform.get("config_file_profile", "DEFAULT"),
                "--region", terraform["region"],
            ]))
            metadata["certificate_ocid"] = imported["data"]["id"]
        finally:
            payload_path.unlink(missing_ok=True)
    secure_write(directory / "metadata.json", json.dumps(metadata, indent=2) + "\n")
    print("Development certificate verified for " + fqdn)
    print("Public CA and certificate metadata saved under .local/pki; server certificate expires in 30 days")
    if metadata["certificate_ocid"]:
        print("OCI certificate imported; configure terraform.mysql_certificate_ocid from metadata.json")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
