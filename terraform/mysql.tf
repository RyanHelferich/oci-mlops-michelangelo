# Approved OCI MySQL compatibility path; disabled until explicitly selected.
data "oci_mysql_mysql_configurations" "base" {
  count          = var.database_mode == "mysql" ? 1 : 0
  compartment_id = var.compartment_ocid
  shape_name     = var.mysql_shape
  display_name   = "${var.mysql_shape}.${var.mysql_high_availability ? "HA" : "Standalone"}"
  type           = ["DEFAULT"]
  state          = "ACTIVE"
  lifecycle {
    postcondition {
      condition     = length(self.configurations) == 1
      error_message = "Exactly one active default MySQL configuration for the chosen shape and HA mode must be available in the region."
    }
  }
}
resource "oci_mysql_mysql_configuration" "secure" {
  count                   = var.database_mode == "mysql" ? 1 : 0
  compartment_id          = var.compartment_ocid
  shape_name              = var.mysql_shape
  parent_configuration_id = one(data.oci_mysql_mysql_configurations.base[0].configurations).id
  display_name            = "${local.name}-mysql-tls"
  description             = "Reject unencrypted TCP; upstream requires a verified TLS transport wrapper."
  # parent_configuration_id is metadata only. Copy the selected HA/Standalone
  # defaults explicitly, replacing just the secure transport option.
  dynamic "options" {
    for_each = { for option in one(data.oci_mysql_mysql_configurations.base[0].configurations).options : option.name => option.value if option.name != "require_secure_transport" }
    content {
      name  = options.key
      value = options.value
    }
  }
  options {
    name  = "require_secure_transport"
    value = "ON"
  }
  freeform_tags = local.tags
}

# OCI MySQL principal networking permissions required for NSG association.
# Scope is the dedicated compartment, never tenancy-wide.
resource "oci_identity_policy" "mysql_network" {
  count          = var.database_mode == "mysql" ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${local.name}-mysql-network"
  description    = "MySQL DB-system principal can attach its private VNIC to compartment NSGs."
  statements = [
    "Allow any-user to {NETWORK_SECURITY_GROUP_UPDATE_MEMBERS} in compartment id ${var.compartment_ocid} where all {request.principal.type = 'mysqldbsystem', request.resource.compartment.id = '${var.compartment_ocid}'}",
    "Allow any-user to {VNIC_CREATE, VNIC_UPDATE, VNIC_ASSOCIATE_NETWORK_SECURITY_GROUP, VNIC_DISASSOCIATE_NETWORK_SECURITY_GROUP} in compartment id ${var.compartment_ocid} where all {request.principal.type = 'mysqldbsystem', request.resource.compartment.id = '${var.compartment_ocid}'}"
  ]
  freeform_tags = local.tags
}
resource "oci_mysql_mysql_db_system" "main" {
  count                   = var.database_mode == "mysql" ? 1 : 0
  compartment_id          = var.compartment_ocid
  availability_domain     = var.availability_domains[0]
  subnet_id               = oci_core_subnet.private["db"].id
  nsg_ids                 = [oci_core_network_security_group.role["db"].id]
  shape_name              = var.mysql_shape
  configuration_id        = oci_mysql_mysql_configuration.secure[0].id
  display_name            = "${local.name}-mysql"
  hostname_label          = var.mysql_hostname_label
  admin_username          = var.mysql_admin_username
  admin_password          = var.mysql_admin_password
  data_storage_size_in_gb = var.mysql_storage_gbs
  is_highly_available     = var.mysql_high_availability
  port                    = 3306
  port_x                  = 33060
  database_management     = "DISABLED"
  secure_connections {
    certificate_generation_type = var.mysql_certificate_ocid == null ? "SYSTEM" : "BYOC"
    certificate_id              = var.mysql_certificate_ocid
  }
  backup_policy {
    is_enabled        = true
    retention_in_days = var.mysql_backup_retention_days
    window_start_time = "04:00"
  }
  deletion_policy {
    is_delete_protected        = var.environment == "prod"
    automatic_backup_retention = var.mysql_backup_retention_on_delete
    final_backup               = var.mysql_final_backup_on_delete
  }
  freeform_tags = local.tags
  depends_on    = [oci_identity_policy.mysql_network, oci_identity_policy.mysql_certificate, oci_core_network_security_group_security_rule.role]
  lifecycle {
    precondition {
      condition     = var.mysql_admin_password != null
      error_message = "database_mode=mysql requires TF_VAR_mysql_admin_password; do not put the secret in central configuration."
    }
    precondition {
      condition     = var.environment != "prod" || var.mysql_high_availability
      error_message = "The prod baseline requires mysql_high_availability=true; backup restore and workload HA still require validation."
    }
    precondition {
      condition     = var.environment != "prod" || (var.mysql_backup_retention_on_delete == "RETAIN" && var.mysql_final_backup_on_delete == "REQUIRE_FINAL_BACKUP")
      error_message = "The prod baseline preserves automatic backups and requires a final backup."
    }
  }
}

resource "oci_identity_policy" "mysql_certificate" {
  count          = var.database_mode == "mysql" && var.mysql_certificate_ocid != null ? 1 : 0
  compartment_id = coalesce(var.mysql_certificate_compartment_ocid, var.compartment_ocid)
  name           = "${local.name}-mysql-certificate"
  description    = "Only MySQL principals from the deployment compartment may read this existing server certificate."
  statements = [
    "Allow any-user to read leaf-certificate-family in compartment id ${coalesce(var.mysql_certificate_compartment_ocid, var.compartment_ocid)} where all {request.principal.type = 'mysqldbsystem', request.resource.compartment.id = '${var.compartment_ocid}', target.leaf-certificate.id = '${var.mysql_certificate_ocid}'}"
  ]
  freeform_tags = local.tags
}
