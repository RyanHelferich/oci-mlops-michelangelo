resource "oci_containerengine_cluster" "main" {
  compartment_id     = var.compartment_ocid
  vcn_id             = oci_core_vcn.main.id
  name               = local.name
  kubernetes_version = var.kubernetes_version
  type               = "ENHANCED_CLUSTER"
  cluster_pod_network_options {
    cni_type = "FLANNEL_OVERLAY"
  }
  endpoint_config {
    is_public_ip_enabled = false
    subnet_id            = oci_core_subnet.private["api"].id
    nsg_ids              = [oci_core_network_security_group.role["api"].id]
  }
  options {
    service_lb_subnet_ids = [oci_core_subnet.private["lb"].id]
    kubernetes_network_config {
      pods_cidr     = "10.244.0.0/16"
      services_cidr = "10.96.0.0/16"
    }
    add_ons {
      is_kubernetes_dashboard_enabled = false
      is_tiller_enabled               = false
    }
  }
  freeform_tags = local.tags
  # Network rules are ready before control-plane bootstrap.
  depends_on = [oci_core_network_security_group_security_rule.role]
}
resource "oci_containerengine_node_pool" "main" {
  compartment_id     = var.compartment_ocid
  cluster_id         = oci_containerengine_cluster.main.id
  name               = "${local.name}-system"
  kubernetes_version = var.kubernetes_version
  node_shape         = var.node_shape
  node_shape_config {
    ocpus         = var.node_ocpus
    memory_in_gbs = var.node_memory_gbs
  }
  node_source_details {
    source_type             = "IMAGE"
    image_id                = var.node_image_ocid
    boot_volume_size_in_gbs = var.node_boot_volume_gbs
  }
  node_config_details {
    size                                = local.node_count
    nsg_ids                             = [oci_core_network_security_group.role["workers"].id]
    is_pv_encryption_in_transit_enabled = true
    dynamic "placement_configs" {
      for_each = toset(var.availability_domains)
      content {
        availability_domain = placement_configs.value
        subnet_id           = oci_core_subnet.private["workers"].id
      }
    }
    node_pool_pod_network_option_details {
      cni_type = "FLANNEL_OVERLAY"
    }
    freeform_tags = local.tags
  }
  initial_node_labels {
    key   = "workload-role"
    value = "system"
  }
  freeform_tags = local.tags
  lifecycle {
    precondition {
      condition     = var.environment != "prod" || (local.node_count >= 3 && length(var.availability_domains) >= 3)
      error_message = "The prod baseline requires at least three nodes across three ADs; region-specific HA designs need explicit review."
    }
  }
}
