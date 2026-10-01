resource "oci_logging_log_group" "main" {
  count          = var.enable_logging ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-infrastructure"
  description    = "Object access and private subnet network flow diagnostics."
  freeform_tags  = local.tags
}
resource "oci_logging_log" "bucket" {
  for_each           = var.enable_logging ? toset(["read", "write"]) : toset([])
  display_name       = "${local.name}-artifact-${each.key}"
  log_group_id       = oci_logging_log_group.main[0].id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = var.log_retention_days
  configuration {
    compartment_id = var.compartment_ocid
    source {
      category    = each.key
      resource    = oci_objectstorage_bucket.artifacts.name
      service     = "objectstorage"
      source_type = "OCISERVICE"
    }
  }
  freeform_tags = local.tags
}
resource "oci_logging_log" "flow" {
  for_each           = var.enable_logging ? oci_core_subnet.private : {}
  display_name       = "${local.name}-${each.key}-flow"
  log_group_id       = oci_logging_log_group.main[0].id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = var.log_retention_days
  configuration {
    compartment_id = var.compartment_ocid
    source {
      category    = "all"
      resource    = each.value.id
      service     = "flowlogs"
      source_type = "OCISERVICE"
    }
  }
  freeform_tags = local.tags
}
