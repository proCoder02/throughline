terraform {
  required_version = ">= 1.5.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 5.0.0"
    }
  }
}

# Auth: reads ~/.oci/config by default (the file `oci setup config` creates
# during the OCI CLI's interactive setup) -- private_key_path below only
# needs to be set explicitly if you're not using the default OCI config
# file/profile layout.
provider "oci" {
  region = var.region
}
