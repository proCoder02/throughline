#!/bin/bash
# Step 5: run the fine-tuned model with Ollama on an Oracle A1 server (Ubuntu).
#   sudo bash setup_ollama.sh <model.gguf> <bind_private_ip> <app_server_private_ip> [same-host]
# "same-host" = this is also the app server: caps the model at 1 core / 5 GB so the website stays fast.
# Safe to re-run. Nothing here is reachable from the internet: Ollama listens on the private IP
# and the firewall only lets the app server in.
set -euo pipefail
GGUF=${1:?gguf path}; BIND=${2:?private ip to listen on}; APP=${3:?app server private ip}; SAME=${4:-}
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh

mkdir -p /etc/systemd/system/ollama.service.d
{
  echo "[Service]"
  echo "Environment=OLLAMA_HOST=${BIND}:11434"
  echo "Environment=OLLAMA_NUM_PARALLEL=1"
  echo "Environment=OLLAMA_MAX_LOADED_MODELS=1"
  echo "Environment=OLLAMA_KEEP_ALIVE=5m"
  if [ "$SAME" = "same-host" ]; then echo "CPUQuota=100%"; echo "MemoryMax=5G"; echo "Nice=10"; fi
} > /etc/systemd/system/ollama.service.d/throughline.conf
systemctl daemon-reload && systemctl restart ollama && sleep 3

# Firewall: only the app server may reach port 11434 (also add the same rule to the VCN security list).
if [ "$SAME" != "same-host" ]; then
  iptables -C INPUT -p tcp -s "$APP" --dport 11434 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp -s "$APP" --dport 11434 -j ACCEPT
  command -v netfilter-persistent >/dev/null && netfilter-persistent save || true
fi

DIR=$(dirname "$(readlink -f "$GGUF")")
cp "$(dirname "$0")/Modelfile" "$DIR/Modelfile"
sed -i "s#^FROM .*#FROM $(readlink -f "$GGUF")#" "$DIR/Modelfile"
OLLAMA_HOST=${BIND}:11434 ollama create throughline-3b -f "$DIR/Modelfile"
OLLAMA_HOST=${BIND}:11434 ollama run throughline-3b 'Reply with JSON only: {"ok": true}' | head -c 200; echo
echo "== ready. On the app server set LOCAL_LLM_URL=http://${BIND}:11434 and restart; unset it to roll back."
