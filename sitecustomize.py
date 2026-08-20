"""
Auto-imported by Python at interpreter startup (see the `site` module docs)
whenever this file is importable — i.e. present in the venv's site-packages,
or (for local `python app.py` runs specifically) in this script's own
directory, since that's on sys.path too. This is what activates CostLens
tracking with zero changes to app.py or any other application source file.

For the gunicorn/systemd production path, sys.path only includes the venv's
site-packages at interpreter startup (not the working directory), so this
file must also be copied there — see infra/terraform/cloud-init.yaml.tftpl,
which does that right after `pip install -r requirements.txt`.
"""

from costlens_agent import install

install()
