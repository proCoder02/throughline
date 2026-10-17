#!/usr/bin/env bash
# Run this ON the production server (/opt/throughline/app), not locally.
# Reports disk + database space before and after seeding, then seeds.
#
# Usage (as the ubuntu user, after scp-ing this file up):
#   chmod +x prod_seed_with_space_check.sh
#   ./prod_seed_with_space_check.sh <exact_prod_username>
#
# <exact_prod_username> must be a real row in the prod `users` table --
# check first with:
#   set -a && source /opt/throughline/.env && set +a
#   psql "$DATABASE_URL" -c "SELECT id, username FROM users ORDER BY id;"

set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "Usage: $0 <exact_prod_username>"
  echo "Check real usernames first: psql \"\$DATABASE_URL\" -c 'SELECT id, username FROM users;'"
  exit 1
fi
USERNAME="$1"

cd /opt/throughline/app
set -a
source /opt/throughline/.env
set +a

echo "=================================================================="
echo "BEFORE seeding"
echo "=================================================================="
echo "--- Disk space (df -h) ---"
df -h /

echo ""
echo "--- Database size ---"
psql "$DATABASE_URL" -c "SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;"
psql "$DATABASE_URL" -c "SELECT count(*) AS user_count FROM users;"
psql "$DATABASE_URL" -c "SELECT count(*) AS direct_message_count FROM direct_messages;"

echo ""
echo "=================================================================="
echo "Seeding now (this calls the LLM per thread -- a few minutes)..."
echo "=================================================================="
/opt/throughline/venv/bin/python seed_demo_data.py --username "$USERNAME"

echo ""
echo "=================================================================="
echo "AFTER seeding"
echo "=================================================================="
echo "--- Disk space (df -h) ---"
df -h /

echo ""
echo "--- Database size ---"
psql "$DATABASE_URL" -c "SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;"
psql "$DATABASE_URL" -c "SELECT count(*) AS user_count FROM users;"
psql "$DATABASE_URL" -c "SELECT count(*) AS direct_message_count FROM direct_messages;"

echo ""
echo "Done. demo_data_manifest.json is in /opt/throughline/app -- run"
echo "  /opt/throughline/venv/bin/python seed_demo_data.py --cleanup"
echo "there whenever you want this removed."
