# Deployment artifacts

Run the standard-library wrapper at `scripts/installer.py` from the repository root.
Configuration belongs in `config/config.json`; the checked-in example has no credentials.
Generated chart packages, values, manifests, review receipts, and plans belong in
ignored `.generated/`. Never commit a generated plan/state or a Secret manifest.

`docs/platform.md` describes source verification, the TLS transport wrapper, Temporal,
review gates, secret synchronization, deployment, and the limits of smoke checks.
The implementation never edits the upstream checkout's tracked files.
