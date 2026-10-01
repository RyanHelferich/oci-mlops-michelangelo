"""Real stock Router/MySQL TLS checks. Creates only disposable local Docker resources.

Requires Docker daemon, cryptography, and PyYAML. No published ports, OCI calls,
or cluster writes. Test credentials/certificates are generated for this run.
"""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("renderer", HERE / "post-renderer.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
ROUTER_IMAGE = "container-registry.oracle.com/mysql/community-router:8.4.6@sha256:468ea2e12477d6c1d8256f575aac33f09a3281531ef982f13bfeea04b1ba02de"
SERVER_IMAGE = "mysql:8.4.6@sha256:869218921e61d6c3c89820955d63cca42971f0e3e6c1e2792247bbd944ebc6e9"


def docker(*args, check=True):
    result = subprocess.run(["docker", *map(str, args)], text=True, capture_output=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError(f"Docker {args[0]} failed: {result.stderr.strip()}")
    return result


def certificates(directory):
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable Router test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
          .not_valid_after(now + datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "db.test")]))
              .issuer_name(ca_name).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName("db.test")]), False).sign(ca_key, hashes.SHA256()))
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Untrusted CA")])
    wrong = (x509.CertificateBuilder().subject_name(wrong_name).issuer_name(wrong_name).public_key(wrong_key.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
             .not_valid_after(now + datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(wrong_key, hashes.SHA256()))
    for name, cert in (("ca.pem", ca), ("server.pem", server), ("wrong-ca.pem", wrong)):
        (directory / name).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "server-key.pem").write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    auth = directory / "auth"
    auth.mkdir()
    router_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    router_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable Router RSA identity")])
    router_cert = (x509.CertificateBuilder().subject_name(router_name).issuer_name(router_name).public_key(router_key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                   .not_valid_after(now + datetime.timedelta(days=1)).sign(router_key, hashes.SHA256()))
    (auth / "tls.crt").write_bytes(router_cert.public_bytes(serialization.Encoding.PEM))
    (auth / "tls.key").write_bytes(router_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))


def main():
    prefix = "ma-router-tls-" + uuid.uuid4().hex[:10]
    network, server = prefix + "-net", prefix + "-db"
    containers = []
    evidence = {"routerImage": ROUTER_IMAGE, "serverImage": SERVER_IMAGE, "checks": {}}
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        directory = Path(tmp)
        certificates(directory)
        probe_directory = directory / "probe"
        probe_directory.mkdir()
        go_probe = probe_directory / "go-probe"
        if shutil.which("go"):
            subprocess.run(["go", "build", "-mod=readonly", "-o", str(go_probe), "."], cwd=HERE / "go-probe", env=dict(os.environ, GOOS="linux", GOARCH="amd64", CGO_ENABLED="0"), check=True, timeout=180)
        try:
            docker("network", "create", network)
            docker("run", "-d", "--name", server, "--network", network, "--network-alias", "db.test", "--network-alias", "wrong.test",
                   "--mount", f"type=bind,source={directory},target=/certs,readonly", "-e", "MYSQL_ROOT_PASSWORD=disposable-root-password", SERVER_IMAGE,
                   "--ssl-ca=/certs/ca.pem", "--ssl-cert=/certs/server.pem", "--ssl-key=/certs/server-key.pem", "--require-secure-transport=ON")
            containers.append(server)
            for attempt in range(60):
                # The image initially starts a temporary socket-only server;
                # wait for the final TCP/TLS server before creating users.
                ready = docker("exec", "-e", "MYSQL_PWD=disposable-root-password", server, "mysql", "-h127.0.0.1", "--ssl-mode=REQUIRED", "-uroot", "-e", "SELECT 1", check=False)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("MySQL test container never became ready: " + docker("logs", server).stdout[-2000:])
            docker("exec", "-e", "MYSQL_PWD=disposable-root-password", server, "mysql", "-uroot", "-e",
                   "CREATE USER 'transport'@'%' IDENTIFIED BY 'disposable-client-password'; GRANT SELECT ON *.* TO 'transport'@'%';")

            def start_router(label, host="db.test", ca="ca.pem", disabled_diagnostic=False):
                name = prefix + "-" + label
                configfile = directory / (label + ".conf")
                config = renderer.router_config(host, 3306)
                if disabled_diagnostic:
                    config = config.replace("client_ssl_mode = PREFERRED", "client_ssl_mode = DISABLED")
                configfile.write_text(config, encoding="utf-8")
                docker("run", "-d", "--name", name, "--network", network, "--network-alias", name,
                       "--user", "65534:65534", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                       "--memory", "128m", "--cpus", "0.25", "--tmpfs", "/router/state:uid=65534,gid=65534,mode=0770,size=16m", "--tmpfs", "/tmp:uid=65534,gid=65534,mode=0770,size=16m",
                       "--mount", f"type=bind,source={configfile},target=/router/config/mysqlrouter.conf,readonly",
                       "--mount", f"type=bind,source={directory / ca},target=/router/ca/ca.pem,readonly",
                       "--mount", f"type=bind,source={directory / 'auth'},target=/router/auth,readonly",
                       "--mount", f"type=bind,source={probe_directory},target=/probe-files,readonly",
                       "--entrypoint", "mysqlrouter", ROUTER_IMAGE, "--config", "/router/config/mysqlrouter.conf")
                containers.append(name)
                for attempt in range(30):
                    ready = docker("exec", name, "/bin/bash", "-ec", "exec 3<>/dev/tcp/127.0.0.1/6446; exec 3>&-", check=False)
                    if ready.returncode == 0:
                        return name
                    time.sleep(0.3)
                raise RuntimeError("Router failed startup: " + docker("logs", name).stderr[-2000:])

            def query(router, host="127.0.0.1", port="6446"):
                return docker("exec", "-e", "MYSQL_PWD=disposable-client-password", router, "mysql", "--connect-timeout=3", "--ssl-mode=DISABLED", "--get-server-public-key", "-h", host, "-P", port, "-utransport", "-N", "-e", "SHOW SESSION STATUS LIKE 'Ssl_cipher'; SELECT 1;", check=False)

            good = start_router("good")
            success = query(good)
            evidence["checks"]["coldCachingSha2LoginSupported"] = success.returncode == 0
            if success.returncode or not any(line.startswith("Ssl_cipher\t") and line.partition("\t")[2].strip() for line in success.stdout.splitlines()):
                raise RuntimeError("Expected cold encrypted backend query failed: " + success.stderr)
            evidence["checks"]["plaintextLoopbackClientHasEncryptedBackend"] = success.stdout.strip().splitlines()
            direct = query(good, "db.test", "3306")
            assert direct.returncode != 0, "TLS-required server accepted direct remote plaintext"
            evidence["checks"]["directRemotePlaintextRejected"] = True
            outside = query(good, good, "6446")
            assert outside.returncode != 0, "Router listened on Pod/container network interface"
            evidence["checks"]["nonLoopbackRouterAccessRejected"] = True
            for label, host, ca in (("wrong-ca", "db.test", "wrong-ca.pem"), ("wrong-host", "wrong.test", "ca.pem")):
                bad = start_router(label, host, ca)
                failed = query(bad)
                assert failed.returncode != 0, label + " unexpectedly authenticated"
                assert "TLS error" in failed.stderr, label + " did not fail certificate validation"
                evidence["checks"][label + "Rejected"] = True
                evidence["checks"][label + "Error"] = failed.stderr.strip()
            docker("exec", "-e", "MYSQL_PWD=disposable-root-password", server, "mysql", "-uroot", "-e", "FLUSH PRIVILEGES")
            cold_again = query(good)
            assert cold_again.returncode == 0, "RSA auth failed after server authentication cache reset"
            evidence["checks"]["coldAuthSucceedsAfterFlushPrivileges"] = True
            docker("restart", server)
            for attempt in range(60):
                ready = docker("exec", "-e", "MYSQL_PWD=disposable-root-password", server, "mysql", "-h127.0.0.1", "--ssl-mode=REQUIRED", "-uroot", "-e", "SELECT 1", check=False)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("MySQL test server failed restart")
            restarted = query(good)
            assert restarted.returncode == 0, "cold auth failed after database restart"
            evidence["checks"]["coldAuthSucceedsAfterDatabaseRestart"] = True
            if go_probe.exists():
                docker("exec", "-e", "MYSQL_PWD=disposable-root-password", server, "mysql", "-uroot", "-e", "FLUSH PRIVILEGES")
                go_result = docker("exec", "-e", "MYSQL_PWD=disposable-client-password", good, "/probe-files/go-probe", check=False)
                assert go_result.returncode == 0, "upstream Go DSN cold authentication failed: " + go_result.stderr
                evidence["checks"]["upstreamGoDriverColdLogin"] = go_result.stdout.strip()
                evidence["goDriverVersion"] = "github.com/go-sql-driver/mysql v1.9.3"
            else:
                evidence["checks"]["upstreamGoDriverColdLogin"] = "SKIPPED: Go compiler unavailable"
            docker("exec", "-e", "MYSQL_PWD=disposable-root-password", server, "mysql", "-uroot", "-e", "FLUSH PRIVILEGES")
            disabled = start_router("disabled-diagnostic", disabled_diagnostic=True)
            broken = query(disabled)
            assert broken.returncode != 0, "legacy DISABLED cold-auth limitation was not reproduced"
            evidence["checks"]["legacyDisabledModeColdAuthRejected"] = broken.stderr.strip()
            no_tls_server = prefix + "-no-tls-db"
            docker("run", "-d", "--name", no_tls_server, "--network", network, "--network-alias", "no-tls.test",
                   "-e", "MYSQL_ROOT_PASSWORD=disposable-root-password", SERVER_IMAGE, "--tls-version=", "--require-secure-transport=OFF")
            containers.append(no_tls_server)
            for attempt in range(60):
                ready = docker("exec", "-e", "MYSQL_PWD=disposable-root-password", no_tls_server, "mysql", "-h127.0.0.1", "--ssl-mode=DISABLED", "--get-server-public-key", "-uroot", "-e", "SELECT 1", check=False)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("No-TLS test server failed startup: " + docker("logs", no_tls_server).stderr[-2000:])
            no_tls_router = start_router("no-tls-router", "no-tls.test")
            rejected = query(no_tls_router)
            assert rejected.returncode != 0 and ("SSL" in rejected.stderr or "TLS" in rejected.stderr), "Router did not reject missing server TLS"
            evidence["checks"]["serverWithoutTlsRejected"] = rejected.stderr.strip()
            evidence["routerVersion"] = docker("exec", good, "mysqlrouter", "--version").stdout.strip()
            evidence["transportSecurityChecksPassed"] = True
            evidence["localTransportRuntimeValidated"] = evidence["checks"]["coldCachingSha2LoginSupported"]
        finally:
            for name in reversed(containers):
                docker("rm", "-f", "-v", name, check=False)
            docker("network", "rm", network, check=False)
    (HERE / "runtime-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
