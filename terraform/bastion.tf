resource "oci_bastion_bastion" "api" {
  count                        = var.enable_bastion ? 1 : 0
  bastion_type                 = "STANDARD"
  compartment_id               = var.compartment_ocid
  target_subnet_id             = oci_core_subnet.private["bastion"].id
  name                         = "ma${var.environment}api"
  client_cidr_block_allow_list = sort(tolist(var.bastion_client_cidrs))
  max_session_ttl_in_seconds   = 3600
  freeform_tags                = local.tags
  lifecycle {
    precondition {
      condition     = length(var.bastion_client_cidrs) > 0
      error_message = "enable_bastion requires at least one explicit operator public /32 allowlist entry."
    }
  }
}
