output "cluster_id" {
  value = oci_containerengine_cluster.main.id
}
output "cluster_private_endpoint" {
  value = oci_containerengine_cluster.main.endpoints[0].private_endpoint
}
output "node_pool_id" {
  value = oci_containerengine_node_pool.main.id
}
output "vcn_id" {
  value = oci_core_vcn.main.id
}
output "subnet_ids" {
  value = { for role, subnet in oci_core_subnet.private : role => subnet.id }
}
output "network_security_group_ids" {
  value = { for role, nsg in oci_core_network_security_group.role : role => nsg.id }
}
output "artifact_bucket_name" {
  value = oci_objectstorage_bucket.artifacts.name
}
output "artifact_namespace" {
  value = data.oci_objectstorage_namespace.main.namespace
}
output "object_storage_s3_endpoint" {
  description = "Commercial OC1 realm S3 compatibility endpoint; workload identity does not authenticate this API."
  value       = "https://${data.oci_objectstorage_namespace.main.namespace}.compat.objectstorage.${var.region}.oraclecloud.com"
}
output "workload_identity" {
  value = {
    cluster_id       = oci_containerengine_cluster.main.id
    namespace        = var.workload_namespace
    service_accounts = sort(tolist(var.workload_service_accounts))
    bucket           = oci_objectstorage_bucket.artifacts.name
    policy_id        = oci_identity_policy.artifact_workload.id
  }
}
output "mysql_endpoint" {
  description = "Private remote endpoint for the TLS sidecar, not the upstream plaintext DSN."
  value = var.database_mode == "mysql" ? {
    id               = oci_mysql_mysql_db_system.main[0].id
    host             = oci_mysql_mysql_db_system.main[0].endpoints[0].hostname
    ip               = oci_mysql_mysql_db_system.main[0].endpoints[0].ip_address
    port             = oci_mysql_mysql_db_system.main[0].endpoints[0].port
    tls_only         = true
    certificate_ocid = var.mysql_certificate_ocid
  } : null
}
output "bastion_id" {
  value = try(oci_bastion_bastion.api[0].id, null)
}
output "mysql_expected_hostname" {
  description = "Expected private DNS name for BYOC certificate SAN; confirm against returned endpoint before use."
  value       = var.database_mode == "mysql" ? "${var.mysql_hostname_label}.db.ma${var.environment}.oraclevcn.com" : null
}
output "artifact_s3_policy_id" {
  value = try(oci_identity_policy.artifact_s3_exception[0].id, null)
}
output "bastion_private_endpoint_ip" {
  value = try(oci_bastion_bastion.api[0].private_endpoint_ip_address, null)
}
output "log_group_id" {
  value = try(oci_logging_log_group.main[0].id, null)
}
output "kubeconfig_command" {
  description = "Run after approved apply; private network reachability or a Bastion tunnel is still required."
  value       = "oci ce cluster create-kubeconfig --cluster-id ${oci_containerengine_cluster.main.id} --region ${var.region} --profile ${var.config_file_profile} --file kubeconfig --token-version 2.0.0 --kube-endpoint PRIVATE_ENDPOINT"
}
