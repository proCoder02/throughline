"""
Thin Grafana Cloud query client -- for Phase 2 (MCO), not Phase 1.

Phase 1's experiments run against the LOCAL database (see data_retrieval.py's
own safety guard) so results aren't contaminated by live production traffic
and don't add experimental load to the real app. The node_exporter/
postgres_exporter metrics flowing into Grafana only monitor the PRODUCTION
box, so they describe a different machine than the one Phase 1 measures --
not a valid per-query cost signal for Phase 1's engineered-side cost (see
mco_asmc/README.md Progress Log for the full reasoning).

What this *is* for: Phase 2's MCO case study, comparing Phase 1's modeled
true cost against what the organization's own monitoring/billing actually
surfaces for the production instance during the period experiments ran.

Confirmed live (2026-10-02): node_exporter and postgres_exporter are both
reporting from the production host into the `grafanacloud-amberolive1722-prom`
Prometheus datasource (Mimir-backed), proxied through Grafana's own API so
the service-account token is the only credential needed.
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

load_dotenv()

GRAFANA_URL = os.getenv("GRAFANA_URL")
GRAFANA_API_TOKEN = os.getenv("GRAFANA_API_TOKEN")
PROM_DATASOURCE_UID = "grafanacloud-prom"


def _require_config() -> None:
    if not GRAFANA_URL or not GRAFANA_API_TOKEN:
        raise RuntimeError("GRAFANA_URL / GRAFANA_API_TOKEN are required (set them in .env)")


def query(promql: str) -> dict:
    """Instant query (PromQL) via Grafana's datasource proxy -- auth is just
    the Grafana service-account token, Grafana handles the datasource's own
    Prometheus credentials internally."""
    _require_config()
    response = requests.get(
        f"{GRAFANA_URL}/api/datasources/uid/{PROM_DATASOURCE_UID}/resources/api/v1/query",
        headers={"Authorization": f"Bearer {GRAFANA_API_TOKEN}"},
        params={"query": promql},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def query_range(promql: str, start: str, end: str, step: str = "60s") -> dict:
    """Range query -- start/end as RFC3339 or unix timestamps."""
    _require_config()
    response = requests.get(
        f"{GRAFANA_URL}/api/datasources/uid/{PROM_DATASOURCE_UID}/resources/api/v1/query_range",
        headers={"Authorization": f"Bearer {GRAFANA_API_TOKEN}"},
        params={"query": promql, "start": start, "end": end, "step": step},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    # Smoke test -- confirms both exporters are up.
    result = query('up{instance="throughline-app"}')
    for series in result.get("data", {}).get("result", []):
        print(series["metric"]["job"], "=", series["value"][1])
