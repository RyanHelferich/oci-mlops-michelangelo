# Contributing

Keep OCI integration outside upstream core code. When a required interface is missing, document the gap and prefer an upstream extension rather than a permanent fork.

Changes must include relevant configuration documentation and meaningful validation. Do not add secrets, local state, private tenancy identifiers, notebook outputs or vault notes to commits. Use the example configuration for reproducible checks, and keep real deployment configuration in the ignored local file.

Document security exceptions and the exact tests completed. A claim of production support needs deployment, failure recovery and workload evidence.
