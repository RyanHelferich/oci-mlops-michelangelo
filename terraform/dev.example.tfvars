# Illustrative values only. Central config/config.example.json is owned by the installer.
# Replace placeholders with region-specific values verified by node-pool-options.
tenancy_ocid              = "ocid1.tenancy.oc1..REPLACE"
compartment_ocid          = "ocid1.compartment.oc1..REPLACE"
region                    = "us-ashburn-1"
config_file_profile       = "DEFAULT"
project_name              = "michelangelo"
environment               = "dev"
kubernetes_version        = "v1.34.10"
node_image_ocid           = "ocid1.image.oc1.iad.REPLACE_WITH_OKE_X86_IMAGE"
availability_domains      = ["REPLACE:US-ASHBURN-AD-1"]
node_shape                = "VM.Standard.E4.Flex"
node_count                = 1
node_ocpus                = 2
node_memory_gbs           = 16
node_boot_volume_gbs      = 50
vcn_cidr                  = "10.42.0.0/16"
artifact_bucket_name      = "REPLACE-unique-michelangelo-dev"
workload_namespace        = "michelangelo"
workload_service_accounts = ["michelangelo-artifacts"]
admin_cidrs               = []
enable_nat_gateway        = true
enable_logging            = false
enable_bastion            = false
bastion_client_cidrs      = []
database_mode             = "none"
# Approved MySQL MVP opt-in: database_mode = "mysql".
# Inject mysql_admin_password only through TF_VAR_mysql_admin_password.
mysql_shape             = "MySQL.2"
mysql_storage_gbs       = 100
mysql_high_availability = false
