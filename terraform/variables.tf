variable "tenancy_ocid" {
  description = "Tenancy OCID used only to resolve Object Storage namespace."
  type        = string
  validation {
    condition     = startswith(var.tenancy_ocid, "ocid1.tenancy.")
    error_message = "Provide a tenancy OCID."
  }
}
variable "compartment_ocid" {
  description = "Existing dedicated child compartment; this stack does not create it."
  type        = string
  validation {
    condition     = startswith(var.compartment_ocid, "ocid1.compartment.")
    error_message = "Provide an existing compartment OCID."
  }
}
variable "region" {
  type    = string
  default = "us-ashburn-1"
}
variable "config_file_profile" {
  type    = string
  default = "DEFAULT"
}
variable "project_name" {
  type    = string
  default = "michelangelo"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,29}$", var.project_name))
    error_message = "Use 2-30 lowercase letters, digits or hyphens, beginning with a letter."
  }
}
variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}
variable "kubernetes_version" {
  description = "Must be supported by both the region and selected OKE node image."
  type        = string
  default     = "v1.34.10"
  validation {
    condition     = can(regex("^v1\\.[0-9]+\\.[0-9]+$", var.kubernetes_version))
    error_message = "Use an explicit OKE Kubernetes version such as v1.34.10."
  }
}
variable "node_image_ocid" {
  description = "Explicit OKE-compatible x86 image OCID from node-pool-options; never infer from a generic compute image."
  type        = string
  validation {
    condition     = startswith(var.node_image_ocid, "ocid1.image.")
    error_message = "Supply a verified OKE node image OCID."
  }
}
variable "availability_domains" {
  description = "Exact region AD names; use one for dev, all intended failure domains for prod."
  type        = list(string)
  validation {
    condition     = length(var.availability_domains) > 0 && length(distinct(var.availability_domains)) == length(var.availability_domains)
    error_message = "Supply at least one unique availability domain."
  }
}
variable "node_shape" {
  type    = string
  default = "VM.Standard.E4.Flex"
  validation {
    condition     = contains(["VM.Standard.E4.Flex", "VM.Standard.E5.Flex", "VM.Standard.E6.Flex"], var.node_shape)
    error_message = "Use a supported x86 flexible shape; ARM/GPU images require separate compatibility verification."
  }
}
variable "node_count" {
  description = "Null selects 1 for dev and 3 for prod; zero can stop dev compute, but other resources still incur charges."
  type        = number
  default     = null
  validation {
    condition     = var.node_count == null ? true : var.node_count >= 0 && floor(var.node_count) == var.node_count
    error_message = "node_count must be null or a nonnegative integer."
  }
}
variable "node_ocpus" {
  type    = number
  default = 2
  validation {
    condition     = var.node_ocpus >= 1 && floor(var.node_ocpus) == var.node_ocpus
    error_message = "Use at least one whole OCPU."
  }
}
variable "node_memory_gbs" {
  type    = number
  default = 16
  validation {
    condition     = var.node_memory_gbs >= 8 && var.node_memory_gbs <= 64 * var.node_ocpus
    error_message = "Use at least 8 GiB and at most 64 GiB per OCPU; verify chosen shape limits."
  }
}
variable "node_boot_volume_gbs" {
  type    = number
  default = 50
  validation {
    condition     = var.node_boot_volume_gbs >= 50 && floor(var.node_boot_volume_gbs) == var.node_boot_volume_gbs
    error_message = "Boot volumes must be at least 50 GiB."
  }
}
variable "vcn_cidr" {
  description = "Private IPv4 /16; subnets derive /24 ranges .0, .1, .2, .3 and .4. Must not overlap connected networks or fixed pod/service CIDRs."
  type        = string
  default     = "10.42.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vcn_cidr)) && can(regex("/16$", var.vcn_cidr)) && try(cidrhost(var.vcn_cidr, 0) == split("/", var.vcn_cidr)[0], false) && (startswith(var.vcn_cidr, "10.") || startswith(var.vcn_cidr, "192.168.") || can(regex("^172\\.(1[6-9]|2[0-9]|3[01])\\.", var.vcn_cidr))) && !contains(["10.244.0.0/16", "10.96.0.0/16"], var.vcn_cidr)
    error_message = "Use a private IPv4 /16 distinct from 10.244.0.0/16 pods and 10.96.0.0/16 services."
  }
}
variable "admin_cidrs" {
  description = "Routed private operator CIDRs allowed to API:6443; empty denies direct operator traffic. Does not create VPN/DRG routes."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for cidr in var.admin_cidrs : can(cidrnetmask(cidr)) && !endswith(cidr, "/0")])
    error_message = "Use explicit IPv4 operator CIDRs; /0 is forbidden."
  }
}
variable "enable_nat_gateway" {
  description = "Outbound-only internet for image pulls/package installation. Disable only with private mirrors and verified bootstrap."
  type        = bool
  default     = true
}
variable "artifact_bucket_name" {
  description = "Unique bucket name within the Object Storage namespace."
  type        = string
  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9._-]{1,253}$", var.artifact_bucket_name))
    error_message = "Use 2-254 safe bucket-name characters."
  }
}
variable "workload_namespace" {
  type    = string
  default = "michelangelo"
  validation {
    condition     = length(var.workload_namespace) <= 63 && can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", var.workload_namespace))
    error_message = "Use a Kubernetes namespace DNS label."
  }
}
variable "workload_service_accounts" {
  description = "Exact service accounts with object read/write/list rights; deployment owner must create and bind them."
  type        = set(string)
  default     = ["michelangelo-artifacts"]
  validation {
    condition     = length(var.workload_service_accounts) > 0 && alltrue([for name in var.workload_service_accounts : length(name) <= 63 && can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name))])
    error_message = "Supply exact Kubernetes service account DNS labels."
  }
}
variable "artifact_allow_delete" {
  description = "Permit runtime object deletion only if artifact lifecycle operations require it."
  type        = bool
  default     = false
}
variable "enable_logging" {
  description = "Enable bucket read/write service logs and private subnet flow logs; no application log collector."
  type        = bool
  default     = false
}
variable "log_retention_days" {
  type    = number
  default = 30
  validation {
    condition     = contains([30, 60, 90, 120, 150, 180], var.log_retention_days)
    error_message = "Use an OCI logging retention interval: 30, 60, 90, 120, 150, 180 days."
  }
}
variable "enable_bastion" {
  description = "Create managed OCI Bastion for temporary API TCP-forwarding sessions; no jump VM."
  type        = bool
  default     = false
}
variable "bastion_client_cidrs" {
  description = "Public operator /32 addresses allowed to initiate managed Bastion sessions."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for cidr in var.bastion_client_cidrs : can(cidrnetmask(cidr)) && endswith(cidr, "/32")])
    error_message = "Use explicit IPv4 /32 addresses for Bastion clients."
  }
}
variable "database_mode" {
  description = "none provisions no database; mysql enables the approved compatible OCI MySQL path. Autonomous is unsupported."
  type        = string
  default     = "none"
  validation {
    condition     = contains(["none", "mysql"], var.database_mode)
    error_message = "Only none or mysql is supported; Autonomous requires an upstream adapter."
  }
}
variable "mysql_shape" {
  type    = string
  default = "MySQL.2"
  validation {
    condition     = can(regex("^MySQL\\.[0-9]+$", var.mysql_shape))
    error_message = "Use a current ECPU MySQL shape such as MySQL.2; no HeatWave cluster is created."
  }
}
variable "mysql_storage_gbs" {
  type    = number
  default = 100
  validation {
    condition     = var.mysql_storage_gbs >= 50 && floor(var.mysql_storage_gbs) == var.mysql_storage_gbs
    error_message = "Supply at least 50 GiB of MySQL storage."
  }
}
variable "mysql_high_availability" {
  description = "Enable MySQL multi-instance HA explicitly; increases cost substantially."
  type        = bool
  default     = false
}
variable "mysql_backup_retention_days" {
  type    = number
  default = 7
  validation {
    condition     = var.mysql_backup_retention_days >= 1 && var.mysql_backup_retention_days <= 35 && floor(var.mysql_backup_retention_days) == var.mysql_backup_retention_days
    error_message = "Use 1-35 days of automatic MySQL backup retention."
  }
}
variable "mysql_backup_retention_on_delete" {
  description = "RETAIN preserves recovery at ongoing storage cost; DELETE requires explicit data-loss review."
  type        = string
  default     = "RETAIN"
  validation {
    condition     = contains(["RETAIN", "DELETE"], var.mysql_backup_retention_on_delete)
    error_message = "Use RETAIN or DELETE."
  }
}
variable "mysql_final_backup_on_delete" {
  description = "REQUIRE_FINAL_BACKUP preserves a final billable backup; SKIP_FINAL_BACKUP is a deliberate dev disposal choice."
  type        = string
  default     = "REQUIRE_FINAL_BACKUP"
  validation {
    condition     = contains(["REQUIRE_FINAL_BACKUP", "SKIP_FINAL_BACKUP"], var.mysql_final_backup_on_delete)
    error_message = "Use REQUIRE_FINAL_BACKUP or SKIP_FINAL_BACKUP."
  }
}
variable "mysql_admin_username" {
  description = "Provisioning administrator; application schema/user setup is a separate operation."
  type        = string
  default     = "ma_admin"
  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,31}$", var.mysql_admin_username)) && !contains(["root", "mysql.sys", "mysql.session", "mysql.infoschema"], var.mysql_admin_username)
    error_message = "Supply a non-reserved MySQL administrator username."
  }
}
variable "mysql_admin_password" {
  description = "Only via TF_VAR_mysql_admin_password or secure runtime injection; sensitive values still reside in Terraform state."
  type        = string
  sensitive   = true
  default     = null
  validation {
    condition     = var.mysql_admin_password == null ? true : length(var.mysql_admin_password) >= 8 && length(var.mysql_admin_password) <= 32 && can(regex("[a-z]", var.mysql_admin_password)) && can(regex("[A-Z]", var.mysql_admin_password)) && can(regex("[0-9]", var.mysql_admin_password)) && can(regex("[^a-zA-Z0-9]", var.mysql_admin_password))
    error_message = "MySQL password must be 8-32 characters containing lower/uppercase, a digit and a special character."
  }
}
variable "artifact_s3_group_ocid" {
  description = "Optional existing dedicated S3 credential group approved as an exception; creates only a bucket-scoped policy, no user or keys."
  type        = string
  default     = null
  validation {
    condition     = var.artifact_s3_group_ocid == null ? true : startswith(var.artifact_s3_group_ocid, "ocid1.group.")
    error_message = "Supply an existing OCI group OCID or null."
  }
}
variable "mysql_hostname_label" {
  description = "Private DNS label; BYOC certificate SAN must match hostname.db.ma<environment>.oraclevcn.com."
  type        = string
  default     = "mysql"
  validation {
    condition     = length(var.mysql_hostname_label) <= 63 && can(regex("^[a-z]([a-z0-9-]*[a-z0-9])?$", var.mysql_hostname_label))
    error_message = "Use a DNS label beginning with a letter and ending with a letter or digit."
  }
}
variable "mysql_certificate_ocid" {
  description = "Existing OCI Certificates server certificate; null uses SYSTEM TLS, whose identity/trust must be verified separately before deploying."
  type        = string
  default     = null
  validation {
    condition     = var.mysql_certificate_ocid == null ? true : startswith(var.mysql_certificate_ocid, "ocid1.certificate.")
    error_message = "Supply an existing OCI certificate OCID or null."
  }
}
variable "mysql_certificate_compartment_ocid" {
  description = "Certificate compartment for its scoped access policy; null uses the deployment compartment."
  type        = string
  default     = null
  validation {
    condition     = var.mysql_certificate_compartment_ocid == null ? true : startswith(var.mysql_certificate_compartment_ocid, "ocid1.compartment.")
    error_message = "Supply an existing certificate compartment OCID or null."
  }
}
variable "tags" {
  description = "Non-sensitive freeform cost attribution tags."
  type        = map(string)
  default     = {}
}
