"""Run a tiny Michelangelo PipelineRun on the existing private deployment.

The harness creates only test resources in the existing namespace and small
objects in the approved artifact bucket. It never starts a Temporal workflow:
the Michelangelo controller owns that operation. Temporal access is read-only.
"""
import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid

from minio import Minio
from temporalio.client import Client
from temporalio.api.enums.v1 import EventType

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/mvp-pipeline"


class ApiError(RuntimeError):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"Michelangelo API HTTP {status}")


def api(base, kind, method, body):
    url = f"{base}/michelangelo.api.v2.{kind}Service/{method}{kind}"
    request = urllib.request.Request(url, json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "rpc-service": "ma-apiserver", "rpc-encoding": "proto",
        "context-ttl-ms": "30000", "grpc-timeout": "30000m",
        "rpc-caller": "oci-mvp-validation", "x-user-name": "oci-mvp-validation",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ApiError(error.code, error.read().decode()) from None


def get_object(client, bucket, key):
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def put_object(client, bucket, key, data, content_type):
    client.put_object(bucket, key, io.BytesIO(data), len(data), content_type=content_type)
    if get_object(client, bucket, key) != data:
        raise RuntimeError("Artifact checksum mismatch")
    return {"uri": f"s3://{bucket}/{key}", "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


@contextlib.contextmanager
def forwards(args, evidence_dir):
    processes = []
    logs = []
    try:
        for service, local_port, remote_port in (
            ("michelangelo-envoy", args.api_port, 8081),
            ("michelangelo-temporal-frontend", args.temporal_port, 7233),
        ):
            log = (evidence_dir / f"{service}-port-forward.log").open("w")
            logs.append(log)
            process = subprocess.Popen([
                "kubectl", "--kubeconfig", str(args.kubeconfig), "-n", args.namespace,
                "port-forward", "--address", "127.0.0.1", f"service/{service}",
                f"{local_port}:{remote_port}",
            ], stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes.append(process)
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Port forward failed for {service}; see local log")
                try:
                    with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"Port forward timed out for {service}")
                    time.sleep(0.25)
        yield
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


async def execute(args, evidence, evidence_dir):
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    deployment = config["deployment"]
    if not (deployment["enable_mutations"] and
            deployment["static_object_storage_credentials_approved"]):
        raise RuntimeError("Configured mutations and scoped S3 credentials must be approved")
    storage = config["platform"]["objectStorage"]
    if storage["secure"] is not True:
        raise RuntimeError("Artifact HTTPS is required")
    credentials = json.loads(args.credentials.read_text(encoding="utf-8-sig"))
    credentials = credentials.get("data", credentials)
    s3 = Minio(storage["endpoint"], access_key=credentials["id"],
               secret_key=credentials["key"], secure=True, region=storage["region"])
    bucket = storage["bucket"]
    run_name = evidence["run_name"]
    prefix = f"_oci_mvp_pipeline/{run_name}"
    dataset = (EXAMPLE / "dataset.json").read_bytes()
    evidence["dataset"] = put_object(s3, bucket, prefix + "/dataset.json", dataset, "application/json")
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode="w:gz") as archive:
        for name, data in {
            "workflow.star": (EXAMPLE / "workflow.star").read_bytes(),
            "meta.json": b'{"main_file":"workflow.star","main_function":"train"}',
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    package_bytes = package.getvalue()
    (evidence_dir / "uniflow.tar.gz").write_bytes(package_bytes)
    evidence["package"] = put_object(s3, bucket, prefix + "/uniflow.tar.gz", package_bytes, "application/gzip")
    base = f"http://127.0.0.1:{args.api_port}"
    namespace = args.namespace
    # The controller fetches a namespaced Project whose name equals the namespace.
    try:
        evidence["project"] = api(base, "Project", "Get", {"namespace": namespace, "name": namespace})
    except ApiError as error:
        if error.status != 404:
            raise
        evidence["project"] = api(base, "Project", "Create", {"project": {
            "metadata": {"name": namespace, "namespace": namespace,
                         "annotations": {"michelangelo/worker_queue": "default"}},
            "spec": {"description": "OCI MVP validation project", "tier": 5,
                     "gitRepo": "https://github.com/michelangelo-ai/michelangelo.git",
                     "rootDir": "examples/mvp-pipeline",
                     "owner": {"owners": ["oci-mvp-validation"], "owningTeam": "00000000-0000-4000-8000-000000000000"}},
        }})
    # ImageBuildActor requires this annotation even when no Ray/Spark task is used.
    # Reuse the already deployed pinned worker image; this workflow launches no pod.
    worker_image = json.loads((ROOT / "upstream.lock.json").read_text())["images"]["worker"]
    pipeline = {"metadata": {"name": run_name, "namespace": namespace,
                            "annotations": {"michelangelo/uniflow-image": worker_image}}, "spec": {
        "description": "Four-row ordinary least squares in Michelangelo UniFlow",
        "owner": {"name": "oci-mvp-validation"},
        "type": "PIPELINE_TYPE_TRAIN", "manifest": {
            "type": "PIPELINE_MANIFEST_TYPE_UNIFLOW", "uniflowTar": evidence["package"]["uri"],
        },
    }}
    evidence["pipeline"] = api(base, "Pipeline", "Create", {"pipeline": pipeline})
    run = {"metadata": {"name": run_name, "namespace": namespace}, "spec": {
        "pipeline": {"name": run_name, "namespace": namespace},
        "input": {"kw_args": {"dataset_url": evidence["dataset"]["uri"]}},
    }}
    evidence["submitted_run"] = api(base, "PipelineRun", "Create", {"pipelineRun": run})
    print(f"Submitted Michelangelo PipelineRun {namespace}/{run_name}", flush=True)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        current = api(base, "PipelineRun", "Get", {"name": run_name, "namespace": namespace})
        evidence["pipeline_run"] = current
        status = current["pipelineRun"].get("status", {})
        if status.get("workflowId") and status.get("workflowRunId"):
            break
        if status.get("state") in ("PIPELINE_RUN_STATE_FAILED", "PIPELINE_RUN_STATE_KILLED"):
            raise RuntimeError("Michelangelo PipelineRun reached a terminal failure before starting workflow")
        await asyncio.sleep(2)
    else:
        raise RuntimeError("Controller did not start a workflow within the timeout")
    temporal = await Client.connect(f"127.0.0.1:{args.temporal_port}", namespace="default")
    # Obtain an existing handle; never call start_workflow or execute_workflow.
    handle = temporal.get_workflow_handle(status["workflowId"], run_id=status["workflowRunId"])
    # Michelangelo uses starlark-worker's custom converter, whose payloads lack
    # the encoding metadata expected by the Python SDK's default converter.
    # Read the completed history payload directly; do not invent a new workflow.
    while time.monotonic() < deadline:
        description = await handle.describe()
        if description.status.name != "RUNNING":
            break
        await asyncio.sleep(1)
    history = await handle.fetch_history()
    evidence["temporal_failure"] = [event.workflow_execution_failed_event_attributes.failure.message
        for event in history.events if event.HasField("workflow_execution_failed_event_attributes")]
    if description.status.name != "COMPLETED":
        raise RuntimeError("Michelangelo Temporal workflow did not complete successfully")
    completion = next(event.workflow_execution_completed_event_attributes
        for event in history.events if event.HasField("workflow_execution_completed_event_attributes"))
    result = json.loads(completion.result.payloads[0].data)
    evidence["temporal"] = {
        "workflow_id": handle.id, "run_id": status["workflowRunId"],
        "type": description.workflow_type, "task_queue": description.task_queue,
        "status": description.status.name,
        "events": [{"id": event.event_id, "type": EventType.Name(event.event_type),
                    **({"activity_type": event.activity_task_scheduled_event_attributes.activity_type.name}
                       if event.HasField("activity_task_scheduled_event_attributes") else {})}
                   for event in history.events],
    }
    evidence["result"] = result
    (evidence_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    evidence["checks"] = {
        "registered_michelangelo_workflow": description.workflow_type == "starlark-workflow",
        "storage_activity_completed": any(
            event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED and
            any(scheduled.event_id == event.activity_task_completed_event_attributes.scheduled_event_id
                and scheduled.activity_task_scheduled_event_attributes.activity_type.name == "Read"
                for scheduled in history.events
                if scheduled.HasField("activity_task_scheduled_event_attributes"))
            for event in history.events),
        "coefficients": math.isclose(result["coefficients"]["slope"], 2.0) and
                        math.isclose(result["coefficients"]["intercept"], 1.0),
        "mse": math.isclose(result["metrics"]["mse"], 0.0, abs_tol=1e-12),
        "predictions": result["predictions"] == [1, 3, 5, 7],
        "workflow_lineage": result["workflow_id"] == handle.id and
                            result["workflow_run_id"] == status["workflowRunId"],
        "dataset_lineage": result["dataset_url"] == evidence["dataset"]["uri"],
    }
    if not all(evidence["checks"].values()):
        raise RuntimeError("Workflow result or execution evidence failed validation")
    evidence["result_artifact"] = put_object(s3, bucket, prefix + "/result.json",
        json.dumps(result, sort_keys=True).encode(), "application/json")
    state = None
    while time.monotonic() < deadline:
        evidence["pipeline_run"] = api(base, "PipelineRun", "Get", {"name": run_name, "namespace": namespace})
        state = evidence["pipeline_run"]["pipelineRun"].get("status", {}).get("state")
        if state in ("PIPELINE_RUN_STATE_SUCCEEDED", "PIPELINE_RUN_STATE_FAILED", "PIPELINE_RUN_STATE_KILLED"):
            break
        await asyncio.sleep(2)
    evidence["checks"]["controller_succeeded"] = state == "PIPELINE_RUN_STATE_SUCCEEDED"
    if not evidence["checks"]["controller_succeeded"]:
        raise RuntimeError("Temporal completed but Michelangelo controller did not report success")
    family = {"metadata": {"name": run_name, "namespace": namespace},
              "spec": {"name": run_name, "description": "OCI four-row regression validation"}}
    evidence["model_family"] = api(base, "ModelFamily", "Create", {"modelFamily": family})
    model = {"metadata": {"name": run_name, "namespace": namespace}, "spec": {
        "description": "Tiny trained regression; persistence and registration performed by validation harness",
        "owner": {"name": "oci-mvp-validation"},
        "kind": "MODEL_KIND_REGRESSION", "algorithm": "ordinary_least_squares",
        "trainingFramework": "Michelangelo Starlark UniFlow", "source": "oci-mvp-validation",
        "sourcePipelineRun": {"name": run_name, "namespace": namespace},
        "modelFamily": {"name": run_name, "namespace": namespace},
        "modelArtifactUri": [evidence["result_artifact"]["uri"]],
    }}
    evidence["model_created"] = api(base, "Model", "Create", {"model": model})
    evidence["model"] = api(base, "Model", "Get", {"name": run_name, "namespace": namespace})
    registered = evidence["model"]["model"]["spec"]
    evidence["checks"]["model_registered_with_lineage"] = (
        registered["sourcePipelineRun"]["name"] == run_name and
        registered["modelArtifactUri"] == [evidence["result_artifact"]["uri"]])
    if not evidence["checks"]["model_registered_with_lineage"]:
        raise RuntimeError("Model registration readback failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Create tiny test resources and artifacts")
    parser.add_argument("--config", type=Path, default=ROOT / "config/config.json")
    parser.add_argument("--credentials", type=Path, default=ROOT / ".local/artifact-customer-key.json")
    parser.add_argument("--kubeconfig", type=Path, default=ROOT / ".local/bastion/kubeconfig")
    parser.add_argument("--namespace", default="michelangelo")
    parser.add_argument("--api-port", type=int, default=18081)
    parser.add_argument("--temporal-port", type=int, default=17233)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Requires --execute; this creates retained small validation resources")
    run_name = "oci-mvp-" + uuid.uuid4().hex[:12]
    evidence_dir = ROOT / ".local/pipeline-validation" / run_name
    evidence_dir.mkdir(parents=True)
    evidence = {"run_name": run_name, "namespace": args.namespace,
                "upstream_revision": json.loads((ROOT / "upstream.lock.json").read_text())["revision"],
                "outcome": "failed", "limitations": [
                    "Starlark worker training only; no Ray/Spark, GPU, deployment, or inference service tested.",
                    "Harness persists the Temporal result and registers model metadata after workflow success.",
                    "Four synthetic rows validate integration and arithmetic, not model quality.",
                ]}
    try:
        with forwards(args, evidence_dir):
            asyncio.run(execute(args, evidence, evidence_dir))
        evidence["outcome"] = "succeeded"
    except Exception as error:
        # Never dump credentials or a potentially sensitive SDK exception.
        evidence["error_type"] = type(error).__name__
        if isinstance(error, ApiError):
            evidence["api_error"] = {"status": error.status, "body": error.body}
        elif isinstance(error, RuntimeError):
            evidence["error"] = str(error)
        elif isinstance(error, KeyError):
            evidence["missing_key"] = str(error)
        print(f"Validation failed ({type(error).__name__}); inspect {evidence_dir}", flush=True)
    finally:
        path = evidence_dir / "evidence.json"
        path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"outcome": evidence["outcome"], "run_name": run_name,
                          "checks": evidence.get("checks", {}), "report_path": str(path)}, indent=2))
    return 0 if evidence["outcome"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
