# Development database certificates

The default OCI MySQL certificate is service-defined. Do not assume it provides the CA chain and hostname identity needed by `VERIFY_IDENTITY`. The secure wrapper supports an existing certificate imported into OCI Certificates and a matching public CA mounted in database client pods.

For a disposable development environment only, `tools/dev_certificate.py` generates an RSA 4096 private development CA and a 30-day server certificate with a SAN matching the Terraform MySQL private DNS name. OCI Certificates imports support RSA 2048 and 4096. It verifies the chain and hostname locally before any import. It refuses production configurations and refuses to overwrite existing certificates. Confirm the imported resource reaches `ACTIVE`; acceptance of the create request alone is insufficient.

```powershell
python tools/dev_certificate.py --config config/config.json --openssl "C:/Program Files/Git/usr/bin/openssl.exe"
```

On systems with OpenSSL on PATH, omit `--openssl`. To generate and import the certificate using the selected OCI profile, add `--import-oci`; the local configuration must explicitly enable mutations. An OCI Certificates import contains the server private key, so the helper uses an ignored local payload file and deletes that payload afterward.

Set `terraform.mysql_certificate_ocid` to the imported identifier recorded in `.local/pki/mysql/metadata.json`. The Terraform database must use the matching hostname label. Mount only `ca.pem` into the Kubernetes CA Secret; the CA private key and server private key never belong in a pod Secret or Terraform input.

The helper follows the current dev VCN DNS convention `mysql.db.madev.oraclevcn.com`. If network DNS naming changes, update and validate this convention before importing a certificate. Production must use the organization's controlled certificate issuance and renewal process.

Imported certificates are not automatically renewed by this helper. Rotate before expiry, update the OCI certificate version/assignment as appropriate, and test both Router and Temporal connections. Preserve the trusted CA only for its intended environment. Keep local private keys under an appropriate OS access policy; `.gitignore` is not an access-control mechanism.

Teardown of Terraform resources does not delete a certificate imported outside Terraform. Track and explicitly schedule deletion of the imported certificate after the database releases it. Certificate services may enforce delayed deletion.

Sources: [OCI MySQL certificates](https://docs.oracle.com/en-us/iaas/mysql-database/doc/advanced-options.html), [certificate access policies](https://docs.oracle.com/en-us/iaas/mysql-database/doc/mandatory-policies-permissions.html), [viewing the service-defined certificate](https://docs.oracle.com/en-us/iaas/mysql-database/doc/troubleshooting-networking.html).
