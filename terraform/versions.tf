terraform {
  required_version = ">= 1.9.8, < 2.0.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "9.8.0"
    }
  }
}

# Credentials remain in ~/.oci/config or the provider's supported environment.
provider "oci" {
  region              = var.region
  config_file_profile = var.config_file_profile
}
