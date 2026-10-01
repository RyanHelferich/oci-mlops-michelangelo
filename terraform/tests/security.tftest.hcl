# Offline mocked plans. No OCI credentials, network API calls or resource creation.
mock_provider "oci" {
  mock_data "oci_core_services" {
    defaults = {
      services = [{
        id          = "ocid1.service.oc1.iad.mock"
        name        = "All IAD Services In Oracle Services Network"
        cidr_block  = "all-iad-services-in-oracle-services-network"
        description = "offline mock"
      }]
    }
  }
  mock_data "oci_objectstorage_namespace" {
    defaults = { namespace = "offline-test" }
  }
  mock_data "oci_mysql_mysql_configurations" {
    defaults = { configurations = [{ id = "ocid1.mysqlconfiguration.oc1.iad.test", options = [{ name = "max_connections", value = "100" }] }] }
  }
}
variables {
  tenancy_ocid         = "ocid1.tenancy.oc1..test"
  compartment_ocid     = "ocid1.compartment.oc1..test"
  node_image_ocid      = "ocid1.image.oc1.iad.test"
  availability_domains = ["test:AD-1"]
  artifact_bucket_name = "offline-artifacts"
}
run "private_defaults_no_database" {
  command = plan
  assert {
    condition     = oci_containerengine_cluster.main.endpoint_config[0].is_public_ip_enabled == false && alltrue([for subnet in oci_core_subnet.private : subnet.prohibit_public_ip_on_vnic && subnet.prohibit_internet_ingress])
    error_message = "API and every subnet must remain private."
  }
  assert {
    condition     = length(oci_mysql_mysql_db_system.main) == 0 && length(oci_bastion_bastion.api) == 0 && length(oci_logging_log.bucket) == 0
    error_message = "Optional database, Bastion and logs must remain disabled by default."
  }
  assert {
    condition     = length(oci_core_security_list.deny_default.ingress_security_rules) == 0 && length(oci_core_security_list.deny_default.egress_security_rules) == 0 && alltrue([for rule in local.network_rules : rule.direction != "INGRESS" || rule.protocol == "1" || try(rule.peer, "internal") != "0.0.0.0/0"])
    error_message = "Default security lists or NSGs must not open internet TCP/UDP ingress."
  }
  assert {
    condition     = oci_objectstorage_bucket.artifacts.access_type == "NoPublicAccess" && oci_objectstorage_bucket.artifacts.versioning == "Enabled" && !contains(local.artifact_permissions, "OBJECT_DELETE")
    error_message = "Artifacts must be private, versioned, and deny workload deletion by default."
  }
}
run "mysql_bastion_logging_opt_in" {
  command = plan
  variables {
    database_mode          = "mysql"
    mysql_admin_password   = "OfflineOnly9!"
    enable_bastion         = true
    bastion_client_cidrs   = ["203.0.113.10/32"]
    enable_logging         = true
    admin_cidrs            = ["10.10.0.1/32"]
    artifact_allow_delete  = true
    mysql_certificate_ocid = "ocid1.certificate.oc1.iad.test"
    artifact_s3_group_ocid = "ocid1.group.oc1..test"
  }
  assert {
    condition     = one([for option in oci_mysql_mysql_configuration.secure[0].options : option.value if option.name == "require_secure_transport"]) == "ON" && one([for option in oci_mysql_mysql_configuration.secure[0].options : option.value if option.name == "max_connections"]) == "100" && oci_mysql_mysql_db_system.main[0].is_highly_available == false
    error_message = "The dev MySQL path must enforce TLS and use a single instance."
  }
  assert {
    condition     = oci_mysql_mysql_db_system.main[0].secure_connections[0].certificate_generation_type == "BYOC" && oci_mysql_mysql_db_system.main[0].secure_connections[0].certificate_id == var.mysql_certificate_ocid && length(oci_identity_policy.mysql_certificate) == 1 && strcontains(oci_identity_policy.mysql_certificate[0].statements[0], "target.leaf-certificate.id = '${var.mysql_certificate_ocid}'") && length(oci_identity_policy.artifact_s3_exception) == 1
    error_message = "BYOC and approved S3 credentials must only grant their configured resources."
  }
  assert {
    condition     = length(oci_logging_log.bucket) == 2 && length(oci_logging_log.flow) == 5 && oci_bastion_bastion.api[0].max_session_ttl_in_seconds == 3600
    error_message = "Opt-in observability and time-limited managed Bastion must be present."
  }
  assert {
    condition     = oci_core_network_security_group_security_rule.role["mysql_from_workers"].protocol == "6" && oci_core_network_security_group_security_rule.role["mysql_from_workers"].tcp_options[0].destination_port_range[0].min == 3306 && contains(local.artifact_permissions, "OBJECT_DELETE")
    error_message = "MySQL classic protocol ingress and artifact deletion opt-in must match configuration."
  }
}
run "mysql_missing_secret_rejected" {
  command = plan
  variables { database_mode = "mysql" }
  expect_failures = [oci_mysql_mysql_db_system.main]
}
run "bastion_missing_allowlist_rejected" {
  command = plan
  variables { enable_bastion = true }
  expect_failures = [oci_bastion_bastion.api]
}
run "prod_underprovisioning_rejected" {
  command = plan
  variables { environment = "prod" }
  expect_failures = [oci_containerengine_node_pool.main]
}
run "prod_ha_baseline" {
  command = plan
  variables {
    environment             = "prod"
    availability_domains    = ["test:AD-1", "test:AD-2", "test:AD-3"]
    database_mode           = "mysql"
    mysql_admin_password    = "OfflineOnly9!"
    mysql_high_availability = true
    enable_nat_gateway      = false
  }
  assert {
    condition     = oci_containerengine_node_pool.main.node_config_details[0].size == 3 && oci_mysql_mysql_db_system.main[0].is_highly_available && oci_mysql_mysql_db_system.main[0].deletion_policy[0].is_delete_protected && length(oci_core_nat_gateway.main) == 0
    error_message = "Prod must require HA and database protection; disabled NAT must disappear."
  }
}
