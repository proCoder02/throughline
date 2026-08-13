# ---------------------------------------------------------------------------
# Networking -- a plain public VCN/subnet, no NAT/private-subnet complexity
# needed for a single always-on box.
# ---------------------------------------------------------------------------

resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  cidr_block     = "10.0.0.0/16"
  display_name   = "throughline-vcn"
  dns_label      = "throughline"
}

resource "oci_core_internet_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "throughline-igw"
}

resource "oci_core_route_table" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "throughline-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.this.id
  }
}

# SSH (22) restricted to your own IP is safer than 0.0.0.0/0, but that
# means re-running terraform (or editing this list by hand) every time your
# home/office IP changes -- left open here for simplicity; tighten
# ssh_ingress_cidr below if you'd rather not.
variable "ssh_ingress_cidr" {
  description = "CIDR allowed to reach SSH (22). Defaults to open -- narrow this to your own IP/32 for real use."
  type        = string
  default     = "0.0.0.0/0"
}

resource "oci_core_security_list" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "throughline-seclist"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  ingress_security_rules {
    source   = var.ssh_ingress_cidr
    protocol = "6" # TCP
    tcp_options {
      min = 22
      max = 22
    }
  }
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "6"
    tcp_options {
      min = 80
      max = 80
    }
  }
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "6"
    tcp_options {
      min = 443
      max = 443
    }
  }
  # ICMP (ping + path-MTU-discovery) -- OCI recommends allowing this even on
  # a locked-down security list; blocking it entirely tends to cause subtle
  # connectivity issues rather than just blocking ping.
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "1"
    icmp_options {
      type = 3
      code = 4
    }
  }
}

resource "oci_core_subnet" "this" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = "10.0.1.0/24"
  display_name               = "throughline-subnet"
  dns_label                  = "app"
  route_table_id             = oci_core_route_table.this.id
  security_list_ids          = [oci_core_security_list.this.id]
  prohibit_public_ip_on_vnic = false
}

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.compartment_ocid
}

# Looked up dynamically rather than a hardcoded OCID -- image OCIDs are
# region-specific and Oracle rotates them as new Ubuntu point releases ship,
# so a pinned value here would silently go stale.
data "oci_core_images" "ubuntu_arm" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "22.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order                = "DESC"
}

resource "oci_core_instance" "app" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "throughline-app"
  shape                = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gbs
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.this.id
    assign_public_ip = true
  }

  source_details {
    source_type            = "image"
    source_id              = data.oci_core_images.ubuntu_arm.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_size_gb
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = base64encode(local.cloud_init_rendered)
  }
}

locals {
  cloud_init_rendered = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    app_repo_url        = var.app_repo_url
    app_repo_branch     = var.app_repo_branch
    app_subdirectory    = var.app_subdirectory
    domain_name         = var.domain_name
    database_password   = var.database_password
    env_file_contents   = local.env_file_contents
    firebase_json_b64   = base64encode(var.firebase_credentials_json)
    has_firebase_creds  = var.firebase_credentials_json != ""
  })

  # Rendered once here (not inline in the .tftpl) so the .env content and
  # the DB password used to actually create the Postgres role can't drift
  # apart from each other.
  #
  # Every value is double-quoted -- setup.sh does `source /opt/throughline/.env`
  # to load these into a real bash shell (not just systemd's EnvironmentFile
  # parser), and an unquoted value containing spaces -- e.g. a Gmail app
  # password like "nnml zrqe obkg vred" -- gets parsed as a command line
  # rather than an assignment, aborting the script under `set -e`. Quoting is
  # safe for both bash `source` and systemd's EnvironmentFile (which also
  # strips surrounding double quotes).
  env_file_contents = join("\n", [
    "DATABASE_URL=\"postgresql://throughline:${var.database_password}@localhost:5432/throughline\"",
    "REDIS_URL=\"redis://localhost:6379/0\"",
    "FLASK_SECRET_KEY=\"${var.flask_secret_key}\"",
    "DEEPGRAM_API_KEY=\"${var.deepgram_api_key}\"",
    "GROQ_API_KEY=\"${var.groq_api_key}\"",
    "sarvam_api=\"${var.sarvam_api_key}\"",
    "olama_api_key=\"${var.ollama_api_key}\"",
    "SMTP_HOST=\"${var.smtp_host}\"",
    "SMTP_PORT=\"${var.smtp_port}\"",
    "SMTP_USERNAME=\"${var.smtp_username}\"",
    "SMTP_PASSWORD=\"${var.smtp_password}\"",
    "LIVEKIT_URL=\"${var.livekit_url}\"",
    "LIVEKIT_API_KEY=\"${var.livekit_api_key}\"",
    "LIVEKIT_API_SECRET=\"${var.livekit_api_secret}\"",
    "FIREBASE_CREDENTIALS_PATH=\"/opt/throughline/firebase-credentials.json\"",
    "USE_GROQ_STT=\"${var.use_groq_stt}\"",
    "EMOTIONAL_INTELLIGENCE_ENABLED=\"${var.emotional_intelligence_enabled}\"",
    "NUDGE_FEATURE_ENABLED=\"${var.nudge_feature_enabled}\"",
    "PORT=\"5000\"",
  ])
}
