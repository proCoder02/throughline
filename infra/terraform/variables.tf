# ---------------------------------------------------------------------------
# OCI account/auth
# ---------------------------------------------------------------------------

variable "region" {
  description = "OCI region, e.g. us-ashburn-1. Always Free ARM capacity varies by region -- if provisioning fails with an out-of-capacity error, this is usually the first thing to change."
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment to create resources in. Using your tenancy's root compartment OCID (shown on the OCI console's Tenancy Details page) is fine for a single-app setup."
  type        = string
}

# ---------------------------------------------------------------------------
# Instance access + sizing
# ---------------------------------------------------------------------------

variable "ssh_public_key" {
  description = "Contents of your SSH public key (e.g. `cat ~/.ssh/id_ed25519.pub`), not a file path -- this gets embedded directly into the instance's metadata."
  type        = string
}

variable "instance_ocpus" {
  description = "Ampere A1 (ARM) OCPUs. Always Free covers up to 4 total across all A1 instances in your tenancy. Starting below the max (2) reduces the odds of an out-of-capacity error on first apply -- bump it later with a second apply once the instance exists."
  type        = number
  default     = 2
}

variable "instance_memory_gbs" {
  description = "RAM in GB. Always Free covers up to 24GB total across all A1 instances."
  type        = number
  default     = 12
}

variable "boot_volume_size_gb" {
  description = "Boot volume size. Always Free covers up to 200GB total block storage."
  type        = number
  default     = 50
}

# ---------------------------------------------------------------------------
# App source
# ---------------------------------------------------------------------------

variable "app_repo_url" {
  description = "Git URL the instance clones on first boot."
  type        = string
  default     = "https://github.com/proCoder02/throughline.git"
}

variable "app_repo_branch" {
  description = "Branch to deploy."
  type        = string
  default     = "master"
}

variable "app_subdirectory" {
  description = "Path within the repo to the Flask app (app.py, requirements.txt, schema.sql) -- set this if the backend isn't at the repo root."
  type        = string
  default     = "."
}

variable "domain_name" {
  description = "Domain pointed at this instance's public IP, for nginx's server_name and certbot. Leave blank to serve over plain HTTP by IP for now (safe to add a domain and re-run later -- nothing else here depends on it)."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# App secrets -- all marked sensitive, all end up in terraform.tfvars
# (gitignored, see .gitignore in this directory -- never commit that file).
# Written into /opt/throughline/.env on the instance by cloud-init.
# ---------------------------------------------------------------------------

variable "database_password" {
  description = "Password for the app's Postgres role (a fresh role+db is created on first boot -- this isn't your OCI account password)."
  type        = string
  sensitive   = true
}

variable "flask_secret_key" {
  description = "Flask session/JWT signing secret -- generate a real random value, e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`."
  type        = string
  sensitive   = true
}

variable "deepgram_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "groq_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "sarvam_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "ollama_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "smtp_host" {
  type    = string
  default = "smtp.gmail.com"
}

variable "smtp_port" {
  type    = number
  default = 587
}

variable "smtp_username" {
  type      = string
  sensitive = true
  default   = ""
}

variable "smtp_password" {
  type      = string
  sensitive = true
  default   = ""
}

variable "livekit_url" {
  type      = string
  sensitive = true
  default   = ""
}

variable "livekit_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "livekit_api_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "firebase_credentials_json" {
  description = "Full contents of the Firebase service-account JSON file (not a path) -- written to /opt/throughline/firebase-credentials.json on the instance."
  type        = string
  sensitive   = true
  default     = ""
}

variable "use_groq_stt" {
  type    = bool
  default = true
}

variable "emotional_intelligence_enabled" {
  type    = bool
  default = true
}

variable "nudge_feature_enabled" {
  type    = bool
  default = false
}
