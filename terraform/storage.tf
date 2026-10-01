data "oci_objectstorage_namespace" "main" {
  compartment_id = var.tenancy_ocid
}
resource "oci_objectstorage_bucket" "artifacts" {
  compartment_id        = var.compartment_ocid
  namespace             = data.oci_objectstorage_namespace.main.namespace
  name                  = var.artifact_bucket_name
  access_type           = "NoPublicAccess"
  storage_tier          = "Standard"
  versioning            = "Enabled"
  object_events_enabled = false
  freeform_tags         = local.tags
  # OCI rejects deletion while objects/versions remain; no purge is implemented.
}

locals {
  artifact_permissions          = concat(["OBJECT_INSPECT", "OBJECT_READ", "OBJECT_CREATE", "OBJECT_OVERWRITE"], var.artifact_allow_delete ? ["OBJECT_DELETE"] : [])
  artifact_permission_condition = join(", ", [for permission in local.artifact_permissions : "request.permission = '${permission}'"])
  workload_conditions = {
    for sa in var.workload_service_accounts : sa => "request.principal.type = 'workload', request.principal.cluster_id = '${oci_containerengine_cluster.main.id}', request.principal.namespace = '${var.workload_namespace}', request.principal.service_account = '${sa}', target.bucket.name = '${oci_objectstorage_bucket.artifacts.name}'"
  }
}
resource "oci_identity_policy" "artifact_workload" {
  compartment_id = var.compartment_ocid
  name           = "${local.name}-artifact-workload"
  description    = "Exact OKE workload identities, one artifact bucket, explicit object operations."
  statements = flatten([
    for sa, conditions in local.workload_conditions : [
      "Allow any-user to manage objects in compartment id ${var.compartment_ocid} where all {${conditions}, any {${local.artifact_permission_condition}}}",
      "Allow any-user to read buckets in compartment id ${var.compartment_ocid} where all {${conditions}, any {request.permission = 'BUCKET_READ', request.permission = 'BUCKET_INSPECT'}}"
    ]
  ])
  freeform_tags = local.tags
}

resource "oci_identity_policy" "artifact_s3_exception" {
  count          = var.artifact_s3_group_ocid == null ? 0 : 1
  compartment_id = var.compartment_ocid
  name           = "${local.name}-s3-credential-exception"
  description    = "Approved existing S3 credential group, single artifact bucket only."
  statements = [
    "Allow group id ${var.artifact_s3_group_ocid} to manage objects in compartment id ${var.compartment_ocid} where all {target.bucket.name = '${oci_objectstorage_bucket.artifacts.name}', any {${local.artifact_permission_condition}}}",
    "Allow group id ${var.artifact_s3_group_ocid} to read buckets in compartment id ${var.compartment_ocid} where all {target.bucket.name = '${oci_objectstorage_bucket.artifacts.name}', any {request.permission = 'BUCKET_READ', request.permission = 'BUCKET_INSPECT'}}"
  ]
  freeform_tags = local.tags
}
