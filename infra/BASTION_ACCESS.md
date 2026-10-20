# Connecting to the production server

## Direct SSH (preferred -- use this first)

A direct, non-expiring SSH key now works against the instance's public IP
-- no bastion session needed at all. Confirmed live (2026-10-20):

```powershell
ssh -i $env:USERPROFILE\.ssh\id_ed25519 ubuntu@129.213.21.239
```

Postgres, directly on the box:

```powershell
ssh -i $env:USERPROFILE\.ssh\id_ed25519 ubuntu@129.213.21.239 "sudo -u postgres psql -d throughline"
```

Or tunnel Postgres to a local port instead, for a local GUI client (pgAdmin,
DBeaver, etc.) or a local script to connect through:

```powershell
ssh -i $env:USERPROFILE\.ssh\id_ed25519 -L 5433:localhost:5432 ubuntu@129.213.21.239
# then connect to localhost:5433 from another terminal/tool while this stays open
```

This key has no 3-hour TTL and needs no `oci bastion session create-managed-ssh`
dance -- use it for everything (deploys, log checks, psql) unless it stops
working, in which case fall back to the Bastion section below.

## Fallback: OCI Bastion managed SSH session

The original `ssh_public_key` baked into the instance at launch
(`infra/terraform/terraform.tfvars`) has no matching private key on this
machine, and OCI does not allow `ssh_authorized_keys` to be changed on a
running instance -- Terraform marks it `forces replacement`, and the OCI API
itself rejects a direct update with `"ssh_authorized_keys metadata field
cannot be updated"`. Do not attempt either of those; both destroy/recreate
the instance.

Before the direct key above was set up, the working path was an **OCI
Bastion managed SSH session**: it pushes a temporary key to the instance for
the session's lifetime (max 10800s / 3 hours) without touching the instance
itself. A session must be recreated each time the previous one expires --
this is the friction the direct key above exists to avoid, so only use this
path if the direct key stops working.

### One-time setup (already done)

- A keypair for this purpose lives at
  `C:\Users\P3INW57\Desktop\speech2text\throughline_deploy_key` (private,
  not committed) / `throughline_deploy_key.pub` (public).
- The `Bastion` Oracle Cloud Agent plugin is enabled on the instance.
- A Bastion service resource exists: see OCIDs below.

If the keypair is ever lost, generate a new one
(`ssh-keygen -t ed25519 -f throughline_deploy_key -N '""'`) -- a new public
key just needs to be supplied to each new session below, no instance change
needed.

### Reference OCIDs

| What | OCID |
|---|---|
| Instance | `ocid1.instance.oc1.iad.anuwcljrlsxwxeacuiylwus7irsgkowwd3vm3ghj2nyotzxxhjj7pq4vzu3q` |
| Compartment (tenancy root) | `ocid1.tenancy.oc1..aaaaaaaarltcprlr3jiwl4dk3sbfzzvw3srxyqdwofy5ivfvyl4lwa7oapeq` |
| Subnet | `ocid1.subnet.oc1.iad.aaaaaaaayk6knl5aw763hetti5dejfxu3bqba62ddo5kh65cuuqxxa24v2ra` |
| Bastion service | `ocid1.bastion.oc1.iad.amaaaaaalsxwxeaaj5fgt7mxo77cdub4zkuqw57swyoiukyyfidxcujt2ieq` |
| Instance private IP | `10.0.1.153` |
| Instance public IP | `129.213.21.239` |

### Creating a new session

**The OCI CLI is already installed and configured on this machine**
(`oci.exe` on PATH, `~/.oci/config` has user/fingerprint/key_file/tenancy/
region all set and confirmed working) -- no need to go through OCI Cloud
Shell at all. Just one gotcha: the config's default region is
`ap-hyderabad-1` (tenancy home region), but this instance/bastion live in
`us-ashburn-1` -- pass `--region us-ashburn-1` explicitly on every command
below, or it fails with a confusing 404 `NotAuthorizedOrNotFound` from the
wrong region's endpoint.

Run locally in PowerShell:

```powershell
$pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDLAP8FhsoAHyTEbWecG3rF94VbJbaIoOUZm/CWZtdSG throughline-deploy-2026"
Set-Content -Path "C:\Users\P3INW57\Desktop\speech2text\deploykey.pub" -Value $pubkey -Encoding ascii

oci bastion session create-managed-ssh `
  --region us-ashburn-1 `
  --bastion-id "ocid1.bastion.oc1.iad.amaaaaaalsxwxeaaj5fgt7mxo77cdub4zkuqw57swyoiukyyfidxcujt2ieq" `
  --display-name "throughline-deploy-session" `
  --key-type PUB `
  --ssh-public-key-file "C:\Users\P3INW57\Desktop\speech2text\deploykey.pub" `
  --target-resource-id "ocid1.instance.oc1.iad.anuwcljrlsxwxeacuiylwus7irsgkowwd3vm3ghj2nyotzxxhjj7pq4vzu3q" `
  --target-os-username ubuntu `
  --target-port 22 `
  --target-private-ip 10.0.1.153 `
  --session-ttl 10800 `
  --wait-for-state SUCCEEDED
```

The response's `resources[0].identifier` is the new session OCID. Fetch its
connection command -- PowerShell's JMESPath escaping for a hyphenated key
like `ssh-metadata` is finicky (`--query` with backtick-escaping errors out
inconsistently), so just parse the JSON directly instead:

```powershell
$result = oci bastion session get --region us-ashburn-1 --session-id <SESSION_OCID> | ConvertFrom-Json
$result.data.'ssh-metadata'.command
```

If CLI auth ever stops working (expired/revoked API key), the fallback is
OCI Cloud Shell (cloud.oracle.com) -- it's pre-authenticated independent of
this machine's config and runs the same `oci` commands, just without the
`--region` gotcha (Cloud Shell prompts region per-command context instead).

### Connecting from this (Windows) machine

Oracle's documented command uses `-W %h:%p` inside `ProxyCommand`, which
**does not substitute correctly through Windows OpenSSH** -- it silently
fails with `Connection to UNKNOWN port 65535 timed out`. Hardcode the target
instead of relying on `%h:%p`.

Add/update this block in `C:\Users\<you>\.ssh\config` with the new session
OCID each time:

```
Host throughline-bastion
  HostName 10.0.1.153
  User ubuntu
  Port 22
  IdentityFile C:/Users/P3INW57/Desktop/speech2text/throughline_deploy_key
  ProxyCommand ssh -i C:/Users/P3INW57/Desktop/speech2text/throughline_deploy_key -W 10.0.1.153:22 -p 22 <SESSION_OCID>@host.bastion.us-ashburn-1.oci.oraclecloud.com
  StrictHostKeyChecking accept-new
```

Then:

```
ssh throughline-bastion
```

## Deploy commands

```
scp ubuntu@129.213.21.239:/opt/throughline/app/app.py ./prod_app.py.remote   # pull current prod state first, always
ssh -i $env:USERPROFILE\.ssh\id_ed25519 ubuntu@129.213.21.239 "cd /opt/throughline/app && sudo git log --oneline -3 && sudo git status --short"
ssh -i $env:USERPROFILE\.ssh\id_ed25519 ubuntu@129.213.21.239 "sudo systemctl status throughline"
```

(Swap in `throughline-bastion` for the host if falling back to the Bastion
session above.)

The repo on the server is root-owned (cloud-init cloned it as root); use
`sudo` for any git/file operation there, not `git config --global --add
safe.directory`.

**Important:** production's `app.py` has had real features deployed
directly (not via `git commit`) in the past -- diff against
`prod_app.py.remote`, never just overwrite it with a local copy.
