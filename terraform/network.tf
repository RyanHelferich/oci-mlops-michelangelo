locals {
  name       = "${var.project_name}-${var.environment}"
  tags       = merge(var.tags, { Project = var.project_name, Environment = var.environment, ManagedBy = "terraform" })
  node_count = coalesce(var.node_count, var.environment == "prod" ? 3 : 1)
  subnets = {
    api     = cidrsubnet(var.vcn_cidr, 8, 0)
    workers = cidrsubnet(var.vcn_cidr, 8, 1)
    lb      = cidrsubnet(var.vcn_cidr, 8, 2)
    db      = cidrsubnet(var.vcn_cidr, 8, 3)
    bastion = cidrsubnet(var.vcn_cidr, 8, 4)
  }
  osn_service = one([for service in data.oci_core_services.regional.services : service if can(regex("^All .* Services In Oracle Services Network$", service.name))])
}

data "oci_core_services" "regional" {}

resource "oci_core_vcn" "main" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = local.name
  dns_label      = "ma${var.environment}"
  freeform_tags  = local.tags
}
resource "oci_core_service_gateway" "main" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-services"
  services {
    service_id = local.osn_service.id
  }
  freeform_tags = local.tags
}
resource "oci_core_nat_gateway" "main" {
  count          = var.enable_nat_gateway ? 1 : 0
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-outbound"
  block_traffic  = false
  freeform_tags  = local.tags
}
resource "oci_core_route_table" "oke" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-oke-egress"
  route_rules {
    destination       = local.osn_service.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.main.id
  }
  dynamic "route_rules" {
    for_each = var.enable_nat_gateway ? [1] : []
    content {
      destination       = "0.0.0.0/0"
      destination_type  = "CIDR_BLOCK"
      network_entity_id = oci_core_nat_gateway.main[0].id
    }
  }
  freeform_tags = local.tags
}
resource "oci_core_route_table" "isolated" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-isolated"
  freeform_tags  = local.tags
}

# Explicit empty lists avoid OCI's default SSH ingress and unrestricted egress.
resource "oci_core_security_list" "deny_default" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-nsg-only"
  freeform_tags  = local.tags
}
resource "oci_core_security_list" "bastion" {
  count          = var.enable_bastion ? 1 : 0
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-bastion-api-only"
  egress_security_rules {
    destination      = local.subnets.api
    destination_type = "CIDR_BLOCK"
    protocol         = "6"
    tcp_options {
      min = 6443
      max = 6443
    }
  }
  freeform_tags = local.tags
}
resource "oci_core_subnet" "private" {
  for_each                   = { for key, cidr in local.subnets : key => cidr if key != "bastion" || var.enable_bastion }
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = each.value
  display_name               = "${local.name}-${each.key}"
  dns_label                  = each.key
  prohibit_public_ip_on_vnic = true
  prohibit_internet_ingress  = true
  route_table_id             = contains(["api", "workers"], each.key) ? oci_core_route_table.oke.id : oci_core_route_table.isolated.id
  security_list_ids          = [each.key == "bastion" ? oci_core_security_list.bastion[0].id : oci_core_security_list.deny_default.id]
  dhcp_options_id            = oci_core_vcn.main.default_dhcp_options_id
  freeform_tags              = local.tags
}
resource "oci_core_network_security_group" "role" {
  for_each       = toset(["api", "workers", "lb", "db"])
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.name}-${each.key}"
  freeform_tags  = local.tags
}

# OKE flannel requires all worker-to-worker traffic and API-to-worker TCP.
# Role-scoped rules follow Oracle's documented minimum network requirements.
locals {
  base_rules = {
    api_to_workers     = { role = "api", direction = "EGRESS", protocol = "6", peer_role = "workers" }
    workers_from_api   = { role = "workers", direction = "INGRESS", protocol = "6", peer_role = "api" }
    workers_internal_i = { role = "workers", direction = "INGRESS", protocol = "all", peer_role = "workers" }
    workers_internal_e = { role = "workers", direction = "EGRESS", protocol = "all", peer_role = "workers" }
    api_oci_services   = { role = "api", direction = "EGRESS", protocol = "6", peer_type = "SERVICE_CIDR_BLOCK", peer = local.osn_service.cidr_block, min = 443, max = 443 }
    workers_oci        = { role = "workers", direction = "EGRESS", protocol = "6", peer_type = "SERVICE_CIDR_BLOCK", peer = local.osn_service.cidr_block }
    workers_dns_udp    = { role = "workers", direction = "EGRESS", protocol = "17", peer = "169.254.169.254/32", min = 53, max = 53 }
    workers_dns_tcp    = { role = "workers", direction = "EGRESS", protocol = "6", peer = "169.254.169.254/32", min = 53, max = 53 }
    workers_ntp        = { role = "workers", direction = "EGRESS", protocol = "17", peer = "169.254.169.254/32", min = 123, max = 123 }
    workers_pmtu_in    = { role = "workers", direction = "INGRESS", protocol = "1", peer = "0.0.0.0/0", icmp_type = 3, icmp_code = 4 }
    workers_pmtu_out   = { role = "workers", direction = "EGRESS", protocol = "1", peer = "0.0.0.0/0", icmp_type = 3, icmp_code = 4 }
    api_pmtu_in        = { role = "api", direction = "INGRESS", protocol = "1", peer_role = "workers", icmp_type = 3, icmp_code = 4 }
    api_pmtu_out       = { role = "api", direction = "EGRESS", protocol = "1", peer_role = "workers", icmp_type = 3, icmp_code = 4 }
    lb_nodeports       = { role = "lb", direction = "EGRESS", protocol = "6", peer_role = "workers", min = 30000, max = 32767 }
    workers_nodeports  = { role = "workers", direction = "INGRESS", protocol = "6", peer_role = "lb", min = 30000, max = 32767 }
    lb_health          = { role = "lb", direction = "EGRESS", protocol = "6", peer_role = "workers", min = 10256, max = 10256 }
    workers_lb_health  = { role = "workers", direction = "INGRESS", protocol = "6", peer_role = "lb", min = 10256, max = 10256 }
  }
  api_worker_rules = merge(
    { for port in [6443, 12250] : "api_from_workers_${port}" => { role = "api", direction = "INGRESS", protocol = "6", peer_role = "workers", min = port, max = port } },
    { for port in [6443, 12250] : "workers_to_api_${port}" => { role = "workers", direction = "EGRESS", protocol = "6", peer_role = "api", min = port, max = port } }
  )
  admin_rules = merge(
    { for cidr in var.admin_cidrs : "admin_api_${cidr}" => { role = "api", direction = "INGRESS", protocol = "6", peer = cidr, min = 6443, max = 6443 } },
    { for cidr in var.admin_cidrs : "admin_lb_${cidr}" => { role = "lb", direction = "INGRESS", protocol = "6", peer = cidr, min = 443, max = 443 } }
  )
  internet_rules = var.enable_nat_gateway ? {
    workers_https = { role = "workers", direction = "EGRESS", protocol = "6", peer = "0.0.0.0/0", min = 443, max = 443 }
    workers_http  = { role = "workers", direction = "EGRESS", protocol = "6", peer = "0.0.0.0/0", min = 80, max = 80 }
  } : {}
  mysql_rules = var.database_mode == "mysql" ? {
    mysql_from_workers = { role = "db", direction = "INGRESS", protocol = "6", peer_role = "workers", min = 3306, max = 3306 }
    workers_to_mysql   = { role = "workers", direction = "EGRESS", protocol = "6", peer_role = "db", min = 3306, max = 3306 }
  } : {}
  bastion_rules = var.enable_bastion ? {
    bastion_to_api = { role = "api", direction = "INGRESS", protocol = "6", peer = "${oci_bastion_bastion.api[0].private_endpoint_ip_address}/32", min = 6443, max = 6443 }
  } : {}
  network_rules = merge(local.base_rules, local.api_worker_rules, local.admin_rules, local.internet_rules, local.mysql_rules, local.bastion_rules)
}
resource "oci_core_network_security_group_security_rule" "role" {
  for_each                  = local.network_rules
  network_security_group_id = oci_core_network_security_group.role[each.value.role].id
  direction                 = each.value.direction
  protocol                  = each.value.protocol
  description               = replace(each.key, "_", " ")
  stateless                 = false
  source                    = each.value.direction == "INGRESS" ? try(oci_core_network_security_group.role[each.value.peer_role].id, each.value.peer) : null
  source_type               = each.value.direction == "INGRESS" ? try(each.value.peer_type, can(each.value.peer_role) ? "NETWORK_SECURITY_GROUP" : "CIDR_BLOCK") : null
  destination               = each.value.direction == "EGRESS" ? try(oci_core_network_security_group.role[each.value.peer_role].id, each.value.peer) : null
  destination_type          = each.value.direction == "EGRESS" ? try(each.value.peer_type, can(each.value.peer_role) ? "NETWORK_SECURITY_GROUP" : "CIDR_BLOCK") : null
  dynamic "tcp_options" {
    for_each = each.value.protocol == "6" && can(each.value.min) ? [each.value] : []
    content {
      destination_port_range {
        min = tcp_options.value.min
        max = tcp_options.value.max
      }
    }
  }
  dynamic "udp_options" {
    for_each = each.value.protocol == "17" && can(each.value.min) ? [each.value] : []
    content {
      destination_port_range {
        min = udp_options.value.min
        max = udp_options.value.max
      }
    }
  }
  dynamic "icmp_options" {
    for_each = each.value.protocol == "1" ? [each.value] : []
    content {
      type = icmp_options.value.icmp_type
      code = icmp_options.value.icmp_code
    }
  }
}
