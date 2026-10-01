# Pipeline validation checklist

[The tiny regression example](../examples/mvp-pipeline/README.md) exercises the
Michelangelo API, PipelineRun controller, Temporal, worker storage activity,
Object Storage and model registry. The harness submits a PipelineRun through
Michelangelo; it does not start a Temporal workflow directly.

## Expected results

| Check | Expected outcome |
| --- | --- |
| PipelineRun | `PIPELINE_RUN_STATE_SUCCEEDED` |
| Workflow type / status | `starlark-workflow` / `COMPLETED` |
| Storage activity | `Read` scheduled, started and completed for the submitted dataset |
| Coefficients | Intercept 1, slope 2, MSE 0 for the four synthetic rows |
| Predictions | `[1, 3, 5, 7]` |
| Dataset / result | Artifact identity and checksum match submitted/retrieved content |
| Model API | Create/Get succeeds with matching source PipelineRun and artifact URI |
| Harness | All nine checks true, zero exit status |

The harness writes full evidence under ignored
`.local/pipeline-validation/<run-name>/` and uniquely named bucket artifacts.
Do not commit evidence containing workflow/resource IDs, bucket names or endpoint
information. Completed CRs can be ingested into MySQL and disappear from kubectl;
check terminal state through the API rather than inferring failure from absence.

## Upstream contracts

The pinned release uses UniFlow archives containing Starlark source and
`meta.json` with `main_file` and `main_function`. The controller retrieves
`manifest.uniflowTar`, requires a Project named for the namespace and the
`michelangelo/uniflow-image` annotation, and starts its registered workflow.
The example references the pinned existing worker; it launches no training pod.

The harness uses the Studio's Envoy JSON RPC headers and reads workflow result
bytes using the upstream payload contract. `workspaceRootDir` is filesystem-only
in this release; the example omits it and explicitly reads the configured S3 URI.

Source contracts in the pinned checkout include:

- `go/components/pipelinerun/actors/executeworkflow.go`
- `go/components/pipelinerun/actors/imagebuild.go`
- `python/michelangelo/uniflow/core/build.py`
- `go/worker/plugins/storage/plugin.go`
- `go/worker/activities/storage/activities.go`
- `go/base/workflowclient/temporalclient/module.go`
- `javascript/packages/rpc/create-fetch-transport.ts`

## Scope

Regression arithmetic runs in the existing Starlark worker; dataset retrieval is
a real Temporal activity. The harness persists returned coefficient JSON and
registers model metadata after workflow completion because the pinned storage
plugin exposes `read` and its model plugin exposes search.

This example qualifies connectivity, orchestration, result integrity and lineage.
It does not qualify workflow-native model pushing, Ray/Spark/GPU workloads,
serving/inference, model quality, production throughput or failure recovery.
The coefficient artifact is not a deployable serving package. Repeat checks after
source/image/certificate upgrades and retain evidence privately.
