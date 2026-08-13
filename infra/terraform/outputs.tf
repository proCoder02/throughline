output "public_ip" {
  value       = oci_core_instance.app.public_ip
  description = "Point your domain's A record here, or hit this directly over HTTP to test before DNS is set up."
}

output "ssh_command" {
  value       = "ssh ubuntu@${oci_core_instance.app.public_ip}"
  description = "cloud-init logs (setup progress/errors) are at /var/log/cloud-init-output.log on the instance."
}
