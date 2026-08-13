# Deploying to Oracle Cloud (Always Free)

Automates everything *after* account signup: VM + networking (Terraform)
and server setup — Postgres, Redis, nginx, the gunicorn service — via
cloud-init on first boot. Re-running `terraform apply` after an account
exists is the only step that needs a human.

## 1. One-time OCI account setup (can't be automated)

1. Sign up at https://www.oracle.com/cloud/free/ (requires identity
   verification + a card for verification only — Always Free resources
   within the free limits are never charged).
2. Install the OCI CLI and run its interactive setup, which generates an
   API key pair and writes `~/.oci/config`:
   ```
   pip install oci-cli
   oci setup config
   ```
   This prompts for your tenancy OCID, user OCID, and region (all shown on
   the OCI console under your account menu → Tenancy/User Settings), and
   generates the API signing key Terraform's OCI provider authenticates
   with automatically via `~/.oci/config` — nothing further to configure
   for that part.
3. Install Terraform: https://developer.hashicorp.com/terraform/install

## 2. Configure and apply

```
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with real values (see comments in that file)
terraform init
terraform plan    # review what it's about to create
terraform apply
```

If it fails with an out-of-capacity error on the A1 (ARM) shape, that's
Oracle's free-tier ARM capacity being temporarily exhausted in that
region — retry later, try a different region (`region` in tfvars), or
lower `instance_ocpus`/`instance_memory_gbs` and raise them again with a
second `apply` once the instance exists.

## 3. Verify

```
terraform output public_ip
curl http://<public_ip>/  # or whatever a real unauthenticated endpoint returns
```

If nothing responds, SSH in and check cloud-init's own log first — most
first-boot failures show up there before they'd show up in the app's own
service:
```
ssh ubuntu@<public_ip>
sudo tail -100 /var/log/cloud-init-output.log
sudo systemctl status throughline
sudo journalctl -u throughline -n 100
```

## 4. Point a domain at it (optional, can be done later)

1. Create an A record for your domain pointing at `public_ip`.
2. Set `domain_name` in `terraform.tfvars` and `terraform apply` again —
   this updates nginx's `server_name` and attempts a certbot run. If DNS
   hadn't propagated yet during that apply, certbot fails harmlessly; SSH
   in and run it by hand once DNS resolves:
   ```
   sudo certbot --nginx -d yourdomain.com
   ```
3. Update the Flutter app's `API_BASE_URL` and the React app's build to
   point at the new domain/IP instead of the local dev machine.

## 5. Redeploying after a code change

The provisioning script is re-runnable — it won't recreate the DB role/
database if they already exist, and pip/systemd steps are naturally
idempotent:
```
ssh ubuntu@<public_ip>
sudo /opt/throughline/setup.sh
sudo systemctl restart throughline
```

## What this does NOT automate

- **Account signup itself** (identity verification is deliberately not
  scriptable by any tool).
- **DNS registration** — buy/configure the domain yourself; this only
  configures the *server side* once you have one.
- **Firebase/LiveKit/Deepgram/Groq account setup** — this deploys your
  existing credentials, it doesn't create those accounts or API keys.
