# Tiny Michelangelo pipeline

`workflow.star` fits ordinary least squares to `dataset.json` inside the existing
Michelangelo worker. The workflow uses the registered `storage.read` plugin,
which schedules a Temporal activity to fetch the dataset from OCI Object Storage.
No image build, Ray/Spark cluster, GPU, or additional cloud resource is required.

The runner packages `workflow.star` with `meta.json` into the exact UniFlow archive
format consumed by the pinned upstream controller. It creates the Pipeline and
PipelineRun through the Envoy JSON API, waits for the controller-created
`starlark-workflow`, checks its activity history and result, and waits for the
Michelangelo PipelineRun to report success. The runner then uploads the model
result and creates/readbacks ModelFamily and Model resources through the same API.
Result persistence and model registration are harness operations after training.

From the repository root, with Python 3.12, `kubectl`, the existing SSH tunnel to
the private cluster on port 16443, approved local configuration, and scoped key:

```powershell
python -m venv examples/mvp-pipeline/.venv
examples/mvp-pipeline/.venv/Scripts/python.exe -m pip install -r examples/mvp-pipeline/requirements.txt
examples/mvp-pipeline/.venv/Scripts/python.exe tools/test_pipeline.py --execute
```

The runner binds API port 18081 and Temporal port 17233 to `127.0.0.1` only and
stops both port forwards on exit. Override them with `--api-port` and
`--temporal-port` if occupied. Credentials are read inside Python from ignored
`.local/artifact-customer-key.json`; do not paste keys into command arguments.
The runner requires approved mutation and S3 compatibility settings in
`config/config.json` and uses `.local/bastion/kubeconfig` by default.

Each run retains uniquely named small artifacts under
`_oci_mvp_pipeline/oci-mvp-<id>/` in the configured bucket and platform records in
the existing namespace. It creates the namespaced Project named `michelangelo`
only if absent, with a synthetic owner/team marker and the `default` worker queue.
It does not overwrite an existing Project. Completed CRs may be ingested into
MySQL and disappear from `kubectl get`; API readback remains the verification path.
No cleanup of historical runs is performed.

Full evidence, the submitted archive, the result, and port-forward logs go to
ignored `.local/pipeline-validation/<run-name>/`. A successful run exits zero and
prints all checks as `true`; any partial failure exits nonzero and retains the
evidence gathered so far. See [pipeline validation](../../docs/pipeline-validation.md)
for the executed run and limitations.
